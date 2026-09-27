"""PST（Outlook 邮件归档）流式拆解入库。

- 解析器：优先 pypff（libpff 的 Python 库，进程内逐封迭代）；无则回退 readpst CLI
  （pst-utils，边拆边产出 .eml，轮询输出目录实现"边拆边入库"）。
- 每封邮件立即落盘为 .eml + 建库文件行（PST 同名文件夹）+ 关联同 KB + 排队解析，
  进度实时写入容器文档 metadata（pst_state/pst_done/pst_total），前端轮询展示。
- 并发上限 3 个邮件解析任务，避免一次涌入打满 LLM/嵌入配额。
"""
import asyncio
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.document import KnowledgeDocument
from app.models.library_file import LibraryFile
from app.models.library_folder import LibraryFolder

logger = logging.getLogger(__name__)

_MAX_PARALLEL_INGEST = 3
_PROGRESS_EVERY = 10  # 每处理 N 封更新一次进度


def _safe_name(name: str) -> str:
    """文件名净化：去路径分隔符与首尾空白。"""
    n = re.sub(r"[\\/:*?\"<>|]", "_", (name or "").strip())
    return n[:120] or "未命名"


async def _get_or_create_pst_folder(
    db: AsyncSession, tenant_id: int, parent_id: int | None, pst_filename: str
) -> LibraryFolder:
    """PST 同名文件夹（去 .pst 后缀），幂等。"""
    name = Path(pst_filename).stem or pst_filename
    existing = (
        await db.execute(
            select(LibraryFolder).where(
                LibraryFolder.tenant_id == tenant_id,
                LibraryFolder.parent_id.is_(parent_id) if parent_id is None
                else LibraryFolder.parent_id == parent_id,
                LibraryFolder.name == name,
                LibraryFolder.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    folder = LibraryFolder(tenant_id=tenant_id, parent_id=parent_id, name=name)
    db.add(folder)
    await db.flush()
    return folder


# ---------------------------------------------------------------------------
# 解析器：pypff 优先，readpst CLI 兜底
# ---------------------------------------------------------------------------


def _pypff_available() -> bool:
    """真 libpff 绑定才有 .open；PyPI 上同名包是无关项目，必须验证 API 存在。"""
    try:
        import pypff

        return hasattr(pypff, "open")
    except ImportError:
        return False


def _readpst_cmd() -> list[str] | None:
    """返回可调用的 readpst 命令；Windows 上尝试 WSL 里的 readpst。"""
    import shutil
    import sys

    exe = shutil.which("readpst")
    if exe:
        return [exe]
    if sys.platform == "win32" and shutil.which("wsl"):
        try:
            r = subprocess.run(
                ["wsl", "bash", "-c", "command -v readpst"],
                capture_output=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                return ["wsl", "readpst"]
        except Exception:
            pass
    return None


def _readpst_available() -> bool:
    return _readpst_cmd() is not None


def _win_to_wsl(p: Path) -> str:
    """Windows 路径转 WSL 路径（D:\\a\\b → /mnt/d/a/b）。"""
    s = str(p).replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def _count_pypff(path: Path) -> int | None:
    """pypff 先算总邮件数（递归累加各文件夹），失败返回 None。"""
    try:
        import pypff

        pst = pypff.open(str(path))
        try:
            def count(folder) -> int:
                n = folder.number_of_sub_messages
                for sub in folder.sub_folders:
                    n += count(sub)
                return n

            return count(pst.get_root_folder())
        finally:
            pst.close()
    except Exception:
        return None


def _iter_pypff_eml(path: Path):
    """pypff 逐封产出 .eml 字节（EmailMessage 重组，附件一并打包）。生成器，纯同步。"""
    import pypff
    from email.message import EmailMessage
    from email.utils import format_datetime

    pst = pypff.open(str(path))
    try:
        def walk(folder):
            for msg in folder.sub_messages:
                yield msg
            for sub in folder.sub_folders:
                yield from walk(sub)

        for msg in walk(pst.get_root_folder()):
            try:
                em = EmailMessage()
                em["Subject"] = (msg.subject or "").strip() or "(无主题)"
                em["From"] = (msg.sender_name or "").strip() or "unknown"
                if getattr(msg, "delivery_time", None):
                    try:
                        em["Date"] = format_datetime(msg.delivery_time)
                    except Exception:
                        pass
                body = msg.plain_text_body
                if isinstance(body, bytes):
                    body = body.decode("utf-8", errors="ignore")
                if not (body or "").strip():
                    html_body = msg.html_body
                    if isinstance(html_body, bytes):
                        html_body = html_body.decode("utf-8", errors="ignore")
                    if (html_body or "").strip():
                        from app.services.ingestion import html_to_text

                        body = html_to_text(html_body)
                em.set_content(body or "")
                for att in msg.attachments or []:
                    try:
                        data = att.read_buffer()
                        if data:
                            em.add_attachment(
                                data, maintype="application", subtype="octet-stream",
                                filename=att.name or "attachment",
                            )
                    except Exception:
                        continue  # 单附件失败不阻断整封
                yield em["Subject"], em.as_bytes()
            except Exception as exc:
                logger.warning("PST 单封邮件读取失败（跳过）: %s", exc)
    finally:
        pst.close()


async def _iter_readpst_eml(path: Path, on_file):
    """readpst CLI：-S 逐封产出 eml；运行期间每秒轮询输出目录，出现新文件即回调（流式）。

    支持 WSL 里的 readpst（Windows 本机无 readpst 时；路径自动转 /mnt/ 形式）。"""
    cmd = _readpst_cmd()
    if not cmd:
        raise RuntimeError("readpst 不可用")
    use_wsl = cmd[0] == "wsl"
    with tempfile.TemporaryDirectory() as tmp:
        out_arg = _win_to_wsl(Path(tmp)) if use_wsl else tmp
        pst_arg = _win_to_wsl(path) if use_wsl else str(path)
        proc = await asyncio.create_subprocess_exec(
            *cmd, "-S", "-r", "-o", out_arg, pst_arg,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        seen: set[Path] = set()

        async def _scan():
            for eml in sorted(Path(tmp).rglob("*.eml")):
                if eml not in seen and eml.stat().st_size > 0:
                    seen.add(eml)
                    await on_file(eml.stem, eml.read_bytes())

        while True:
            await _scan()
            if proc.returncode is not None:
                await asyncio.sleep(0.3)  # 等输出目录落盘完，最后一轮扫描后结束
                await _scan()
                break
            await asyncio.sleep(1.0)
        err = await proc.stderr.read() if proc.stderr else b""
        if proc.returncode not in (0, None):
            raise RuntimeError(f"readpst 失败: {err.decode(errors='replace')[:300]}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def process_pst(doc_id: int) -> None:
    """PST 容器文档的处理：流式拆邮件 → 逐封入库（文件行 + KB 文档 + 排队解析）。

    任何失败只把容器文档标记 failed，绝不抛出。进度写 doc.doc_metadata：
    pst_state(extracting/ingesting/done/failed), pst_done, pst_total。
    """
    async with AsyncSessionLocal() as session:
        doc = await session.get(KnowledgeDocument, doc_id)
        if doc is None:
            logger.warning("process_pst: 文档 %s 不存在", doc_id)
            return
        try:
            file = await session.get(LibraryFile, doc.file_id)
            if file is None:
                raise ValueError("PST 文件记录不存在")
            path = Path(doc.file_path)
            if not path.exists():
                raise ValueError("PST 文件不在磁盘上")

            pst_folder = await _get_or_create_pst_folder(
                session, doc.tenant_id, file.folder_id, file.file_name
            )
            # 容器文档关联的 KB 集合（邮件文档跟随同 KB）
            kb_ids = [doc.kb_id] if doc.kb_id else []

            meta = dict(doc.doc_metadata or {})
            meta.update({"pst_state": "extracting", "pst_done": 0})
            doc.doc_metadata = meta
            doc.status = "processing"
            await session.commit()

            total = _count_pypff(path) if _pypff_available() else None

            async def _progress(done: int, state: str = "ingesting"):
                async with AsyncSessionLocal() as s2:
                    d = await s2.get(KnowledgeDocument, doc_id)
                    if d is None:
                        return
                    m = dict(d.doc_metadata or {})
                    m.update({"pst_state": state, "pst_done": done, "pst_total": total})
                    d.doc_metadata = m
                    await s2.commit()

            done = 0
            ingest_sem = asyncio.Semaphore(_MAX_PARALLEL_INGEST)
            ingest_tasks: list[asyncio.Task] = []

            async def _ingest_eml(file_name: str, eml_bytes: bytes, sender: str = ""):
                nonlocal done
                stored = settings.upload_path / f"{uuid4().hex}.eml"
                await asyncio.to_thread(stored.write_bytes, eml_bytes)
                async with AsyncSessionLocal() as s3:
                    child = LibraryFile(
                        tenant_id=doc.tenant_id,
                        folder_id=pst_folder.id,
                        file_name=_safe_name(file_name) + ".eml",
                        file_path=str(stored),
                        file_type="eml",
                        file_size=len(eml_bytes),
                        supported=True,
                        owner_id=file.owner_id,
                        is_private=file.is_private,
                    )
                    s3.add(child)
                    await s3.flush()
                    child_doc_ids = []
                    for kb_id in kb_ids:
                        child_doc = KnowledgeDocument(
                            tenant_id=doc.tenant_id,
                            kb_id=kb_id,
                            file_id=child.id,
                            title=child.file_name,
                            file_name=child.file_name,
                            file_path=str(stored),
                            file_type="eml",
                            status="processing",
                        )
                        s3.add(child_doc)
                        await s3.flush()
                        child_doc_ids.append(child_doc.id)
                    await s3.commit()
                from app.services.ingestion import process_document

                async def _run(cid: int):
                    async with ingest_sem:
                        await process_document(cid)

                for cid in child_doc_ids:
                    ingest_tasks.append(asyncio.create_task(_run(cid)))
                done += 1
                if done % _PROGRESS_EVERY == 0:
                    await _progress(done)

            if _pypff_available():
                # pypff 是同步生成器：在线程里逐封取，回主循环入库
                loop = asyncio.get_running_loop()
                gen = _iter_pypff_eml(path)
                while True:
                    item = await loop.run_in_executor(None, next, gen, None)
                    if item is None:
                        break
                    subject, eml_bytes = item
                    await _ingest_eml(subject, eml_bytes)
            elif _readpst_available():
                await _iter_readpst_eml(path, lambda name, data: _ingest_eml(name, data))
            else:
                raise RuntimeError(
                    "PST 解析器不可用：请安装 pypff（python3-pypff）或 pst-utils（readpst）"
                )

            if ingest_tasks:
                await asyncio.gather(*ingest_tasks, return_exceptions=True)
            await _progress(done, "done")
            async with AsyncSessionLocal() as s4:
                d = await s4.get(KnowledgeDocument, doc_id)
                if d is not None:
                    d.status = "ready"
                    d.content = f"PST 邮件归档：共拆出 {done} 封邮件，已保存到「{pst_folder.name}」文件夹并逐封解析入库。"
                    d.chunk_count = 0
                    await s4.commit()
            logger.info("PST 拆解完成 doc=%s: %d 封", doc_id, done)
        except Exception as exc:
            logger.exception("PST 处理失败 doc=%s", doc_id)
            async with AsyncSessionLocal() as s5:
                d = await s5.get(KnowledgeDocument, doc_id)
                if d is not None:
                    d.status = "failed"
                    m = dict(d.doc_metadata or {})
                    m.update({"pst_state": "failed", "error": str(exc)[:300]})
                    d.doc_metadata = m
                    await s5.commit()
            from app.services.error_log import log_error

            await log_error("error", "ingestion", f"PST 文档 {doc_id} 处理失败", str(exc), tenant_id=doc.tenant_id)


async def get_pst_progress(db: AsyncSession, tenant_id: int, file_id: int) -> dict:
    """PST 拆解进度（前端上传面板轮询）。"""
    doc = (
        await db.execute(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.file_id == file_id,
                KnowledgeDocument.tenant_id == tenant_id,
            )
            .order_by(KnowledgeDocument.id.desc())
        )
    ).scalars().first()
    if doc is None:
        return {"state": "unknown"}
    meta = doc.doc_metadata or {}
    state = meta.get("pst_state")
    if not state:
        state = {"processing": "extracting", "ready": "done", "failed": "failed"}.get(
            doc.status, "unknown"
        )
    return {
        "state": state,
        "done": meta.get("pst_done", 0),
        "total": meta.get("pst_total"),
        "error": meta.get("error"),
    }
