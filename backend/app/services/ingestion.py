import asyncio
import base64
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import delete, select
from sqlalchemy import text as sql_text  # 别名避免与 process_document 内 `text` 局部变量冲突

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.chunk import DocumentChunk
from app.models.chunk_question import ChunkQuestion
from app.models.document import KnowledgeDocument
from app.services.llm import resolve_asr_llm, resolve_chat_llm, resolve_embed_llm, resolve_vision_llm
from app.services.ocr import ocr_image_bytes, ocr_provider_name

logger = logging.getLogger(__name__)

TEXT_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm", ".xlsx", ".xls", ".pptx", ".ppt"}
EMAIL_EXTS = {".eml", ".msg", ".pst"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".amr"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
SUPPORTED_EXTS = TEXT_EXTS | EMAIL_EXTS | IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS
# 兼容旧引用
SUPPORTED_TYPES = SUPPORTED_EXTS

# 解析格式目录（系统设置 → 解析文件格式 展示用，含分组与中文名）
FORMAT_CATALOG = [
    {"key": "text", "label": "文本与办公文档", "exts": [".pdf", ".docx", ".doc", ".txt", ".md", ".html", ".htm", ".xlsx", ".xls", ".pptx", ".ppt"]},
    {"key": "email", "label": "邮件", "exts": [".eml", ".msg", ".pst"]},
    {"key": "image", "label": "图片（视觉模型识别）", "exts": [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"]},
    {"key": "audio", "label": "音频（语音转写）", "exts": [".mp3", ".wav", ".m4a", ".ogg", ".flac", ".amr"]},
    {"key": "video", "label": "视频（抽音轨转写）", "exts": [".mp4", ".mov", ".avi", ".mkv", ".webm"]},
]
# 解析格式开关配置在 system_settings 的 key
PARSE_FORMATS_KEY = "parse_formats_enabled"

# 视觉/转写提示词（模块常量，便于调整）
PROMPT_IMAGE_OCR = "请转录图片中的所有文字，并详细描述图片内容。直接输出文本，不要添加解释。"
PROMPT_PDF_PAGE_OCR = "这是扫描版 PDF 的一页。请转录页面中的所有文字，保留原有段落结构。直接输出文本，不要添加解释。"
# 推理型视觉模型（如 MiniMax）输出里夹带的思考块，入库前剥离
_THINK_RE = re.compile(r"<(?:think|thinking)>.*?</(?:think|thinking)>", re.I | re.S)


def _strip_think(text: str) -> str:
    """剥离模型输出中的 <think>…</think> / <thinking>…</thinking> 思考块。纯函数。"""
    if not text:
        return text
    return _THINK_RE.sub("", text).strip()
# 扫描版 PDF 判定：pypdf 提取的总字符数 < 页数 × 该值 → 走视觉识别
PDF_SCAN_MIN_CHARS_PER_PAGE = 20
# 视觉识别页数上限，防止超大 PDF 打爆模型调用
PDF_MAX_OCR_PAGES = 50

_IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}


def _read_image_for_vision(path: Path, ext: str) -> tuple[bytes, str]:
    """图片读入供视觉模型使用；tif/tiff 浏览器与多数视觉模型不认，先转 PNG。"""
    if ext in (".tif", ".tiff"):
        import io

        from PIL import Image

        img = Image.open(path)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue(), "image/png"
    return path.read_bytes(), _IMAGE_MIME[ext]

# doc_metadata["processing_method"] 取值 → doc.content 前缀
_CONTENT_PREFIX = {
    "image_describe": "[图片识别] ",
    "vision_ocr": "[扫描件识别] ",
    "ocr": "[OCR] ",
    "asr": "[音频转写] ",
    "video_asr": "[视频转写] ",
    "email": "[邮件] ",
}


# 当前启用解析的扩展名缓存：None=尚未加载，按全开（SUPPORTED_EXTS）处理
_enabled_parse_exts: set[str] | None = None


def enabled_parse_exts() -> set[str]:
    """当前启用解析的扩展名集合（默认全开，系统设置里可关闭部分格式）。纯函数。"""
    return _enabled_parse_exts if _enabled_parse_exts is not None else SUPPORTED_EXTS


async def _load_enabled_parse_exts(db) -> set[str]:
    """从 system_settings 读启用格式；未配置/损坏时回退全开。纯异步读。"""
    from app.models.system_setting import SystemSetting

    row = await db.get(SystemSetting, PARSE_FORMATS_KEY)
    if row is None or not row.value:
        return set(SUPPORTED_EXTS)
    try:
        data = json.loads(row.value)
        return {ext for ext in data if ext in SUPPORTED_EXTS}
    except (ValueError, TypeError):
        return set(SUPPORTED_EXTS)


async def refresh_enabled_parse_exts(db=None) -> None:
    """刷新进程内启用格式缓存：启动时与系统设置修改后调用。db 缺省时开独立会话。"""
    global _enabled_parse_exts
    try:
        if db is None:
            async with AsyncSessionLocal() as s:
                _enabled_parse_exts = await _load_enabled_parse_exts(s)
        else:
            _enabled_parse_exts = await _load_enabled_parse_exts(db)
    except Exception as exc:
        logger.warning("解析格式配置读取失败（按全开处理）: %s", exc)
        _enabled_parse_exts = None


async def set_enabled_parse_exts(db, exts: list[str]) -> set[str]:
    """保存启用格式并同步刷新缓存，返回实际生效集合。"""
    global _enabled_parse_exts
    from app.models.system_setting import SystemSetting

    valid = {ext for ext in exts if ext in SUPPORTED_EXTS}
    row = await db.get(SystemSetting, PARSE_FORMATS_KEY)
    if row is None:
        db.add(SystemSetting(key=PARSE_FORMATS_KEY, value=json.dumps(sorted(valid))))
    else:
        row.value = json.dumps(sorted(valid))
    _enabled_parse_exts = valid
    await db.commit()
    return _enabled_parse_exts


def is_supported(filename: str) -> bool:
    """当前环境是否解析该格式（默认全开，系统设置里可关闭某些格式）。纯函数。

    判定依据是"启用集合"而非全部能力清单：关闭的格式在上传/关联/重试时
    一律视为不支持，不做解析，避免无用功。"""
    from pathlib import Path as _P

    return _P(filename or "").suffix.lower() in enabled_parse_exts()


def _read_text_file(path: Path) -> str:
    """读文本文件：utf-8 优先，失败回退 gb18030（兼容中文 Windows 遗留编码），最后忽略错误兜底。"""
    data = path.read_bytes()
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


class _HtmlTextExtractor(HTMLParser):
    """HTML → 纯文本：跳过 script/style，块级标签转换行。无第三方依赖。"""

    _SKIP = {"script", "style", "noscript"}
    _BLOCK = {
        "p", "div", "br", "li", "tr", "table", "ul", "ol", "hr", "pre",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer", "blockquote", "title",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif self._skip_depth == 0 and tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif self._skip_depth == 0 and tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data.replace("\xa0", " "))  # &nbsp; 归一化为普通空格，利于下游切分

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    """剥离 HTML 标签得到纯文本（script/style 内容丢弃）。纯函数。"""
    parser = _HtmlTextExtractor()
    parser.feed(html)
    return parser.text()


def parse_file(path: Path, file_type: str) -> str:
    """按扩展名解析文件为纯文本。"""
    ft = file_type.lower().lstrip(".")
    if ft in ("txt", "md"):
        return _read_text_file(path)
    if ft in ("html", "htm"):
        return html_to_text(_read_text_file(path))
    if ft == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if ft == "docx":
        from docx import Document

        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    if ft == "xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(str(path), read_only=True, data_only=True)
        try:
            parts = []
            for ws in wb.worksheets:
                parts.append(f"【工作表：{ws.title}】")
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c).strip() if c is not None else "" for c in row]
                    if any(cells):
                        parts.append("\t".join(cells))
            return "\n".join(parts)
        finally:
            wb.close()
    if ft == "xls":
        import xlrd

        wb = xlrd.open_workbook(str(path))
        parts = []
        for sheet in wb.sheets():
            parts.append(f"【工作表：{sheet.name}】")
            for r in range(sheet.nrows):
                cells = [str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)]
                if any(cells):
                    parts.append("\t".join(cells))
        return "\n".join(parts)
    if ft == "pptx":
        from pptx import Presentation

        prs = Presentation(str(path))
        parts = []
        for i, slide in enumerate(prs.slides, 1):
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    t = "\n".join(
                        p.text for p in shape.text_frame.paragraphs if p.text.strip()
                    )
                    if t.strip():
                        texts.append(t)
                if getattr(shape, "has_table", False) and shape.has_table:
                    for row in shape.table.rows:
                        texts.append("\t".join(cell.text.strip() for cell in row.cells))
            if texts:
                parts.append(f"【第{i}页】\n" + "\n".join(texts))
        return "\n\n".join(parts)
    if ft in ("doc", "ppt"):
        return _read_legacy_office(path, ft)
    raise ValueError(f"不支持的文件类型: {file_type}")


def _read_legacy_office(path: Path, ft: str) -> str:
    """老 Office 二进制（.doc/.ppt）双层提取：
    1) LibreOffice（soffice 在 PATH 时）headless 转 txt——质量最好
    2) olefile 粗提取 WordDocument/PowerPoint Document 流的可读文本——兜底
    """
    text = _read_office_via_soffice(path)
    if text and text.strip():
        return text
    return _read_office_via_olefile(path, ft)


def _read_office_via_soffice(path: Path) -> str | None:
    """LibreOffice headless 转 txt。未安装/失败返回 None。"""
    import shutil
    import subprocess
    import tempfile

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "txt:Text", "--outdir", tmp, str(path)],
                capture_output=True, timeout=120, check=False,
            )
        except Exception as exc:
            logger.warning("soffice 转换失败: %s", exc)
            return None
        outs = list(Path(tmp).glob("*.txt"))
        if not outs:
            return None
        data = outs[0].read_bytes()
        for enc in ("utf-8", "gb18030"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="ignore")


def _read_office_via_olefile(path: Path, ft: str) -> str:
    """olefile 打开 OLE 复合文档提取文本。doc 先按 FIB 正文边界（fcMin/fcMac）精确截取；
    复杂文档/解析失败回退整流扫描。"""
    import re
    import struct

    import olefile

    stream_name = "WordDocument" if ft == "doc" else "PowerPoint Document"
    ole = olefile.OleFileIO(str(path))
    try:
        if not ole.exists(stream_name):
            raise ValueError(f"不是有效的 .{ft} 文件")
        data = ole.openstream(stream_name).read()
    finally:
        ole.close()

    if ft == "doc":
        text = _doc_text_by_fib(data, struct, re)
        if text:
            return text
    return _office_stream_scan(data, ft, re)


def _doc_text_by_fib(data: bytes, struct, re) -> str | None:
    """Word 二进制 FIB：flags(0x0A) bit2=fComplex；fcMin(0x18)/fcMac(0x1C) 为正文字节边界。
    仅处理非复杂文档（fComplex=0 时正文连续存储）。失败返回 None。"""
    try:
        if len(data) < 0x20 or struct.unpack_from("<H", data, 0)[0] != 0xA5EC:
            return None
        flags = struct.unpack_from("<H", data, 0x0A)[0]
        if flags & 0x0004:  # fComplex：正文分片存于 CLX，需分段重组，回退整流扫描
            return None
        fc_min, fc_mac = struct.unpack_from("<II", data, 0x18)
        if fc_mac <= fc_min or fc_mac > len(data):
            return None
        # fWhichTblStm 不影响正文区；非复杂文档正文恒为 UTF-16LE
        text = data[fc_min:fc_mac].decode("utf-16-le", errors="ignore")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "\n", text)
        parts = [p.strip() for p in text.split("\n") if p.strip()]
        result = "\n".join(parts)
        return result if result.strip() else None
    except Exception:
        return None


def _office_stream_scan(data: bytes, ft: str, re) -> str:
    """整流扫描兜底：UTF-16LE 解码 → 过滤控制符 → 保留可读片段。"""
    text = data.decode("utf-16-le", errors="ignore")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]", "\n", text)
    parts = [p.strip() for p in text.split("\n") if len(p.strip()) >= 4]
    # 滤掉二进制流头噪声行：正文行应含常用汉字或主要为可打印 ASCII
    def _is_real(s: str) -> bool:
        cjk = sum(1 for c in s if "一" <= c <= "鿿")
        ascii_printable = sum(1 for c in s if c.isascii() and c.isprintable())
        return cjk > 0 or ascii_printable / max(len(s), 1) > 0.8

    parts = [p for p in parts if _is_real(p)]
    result = "\n".join(parts)
    if not result.strip():
        raise ValueError(f"无法从 .{ft} 提取文本（可安装 LibreOffice 获得完整支持）")
    return result


def clean_text(text: str) -> str:
    """合并多余空白。"""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 切片 v2：Markdown 标题锚定 + 中文断句 + 代码块掩码 + 章节链路
# ---------------------------------------------------------------------------

# 6 级 Markdown 标题正则（参照 MaxKB split_model.py default_split_pattern['md']）
_TITLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?<=^)# .*|(?<=\n)# .*"),
    re.compile(r"(?<=\n)(?<!#)## (?!#).*|(?<=^)(?<!#)## (?!#).*"),
    re.compile(r"(?<=\n)(?<!#)### (?!#).*|(?<=^)(?<!#)### (?!#).*"),
    re.compile(r"(?<=\n)(?<!#)#### (?!#).*|(?<=^)(?<!#)#### (?!#).*"),
    re.compile(r"(?<=\n)(?<!#)##### (?!#).*|(?<=^)(?<!#)##### (?!#).*"),
    re.compile(r"(?<=\n)(?<!#)###### (?!#).*|(?<=^)(?<!#)###### (?!#).*"),
]
_CODE_FENCE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
# 段落切分：双换行（中间可有空格）
_PARA_SPLIT = re.compile(r"\n\s*\n")


def _mask_code_blocks(text: str) -> str:
    """代码块 ```...``` 内的 # 替换为等长空格，防止被识别为标题。
    行内 # （如 Issue #123）不会被误识别，因为标题正则要求 # 后接空格。"""
    if "```" not in text:
        return text
    result = list(text)
    for m in _CODE_FENCE.finditer(text):
        first_nl = text.index("\n", m.start()) + 1
        closing = text.rindex("```", m.start(), m.end())
        for i in range(first_nl, closing):
            if result[i] != "\n":
                result[i] = " "
    return "".join(result)


def _parse_titles(text: str) -> list[dict]:
    """提取所有 Markdown 标题（代码块内 # 已掩码），返回按位置升序的列表。

    每条 {level: int 1-6, content: str, start: int, end: int}。"""
    masked = _mask_code_blocks(text)
    titles: list[dict] = []
    for level_idx, pat in enumerate(_TITLE_PATTERNS, start=1):
        for m in pat.finditer(masked):
            raw = m.group(0).strip()
            content = re.sub(r"^#+\s*", "", raw).strip()
            if not content:
                continue
            titles.append(
                {"level": level_idx, "content": content, "start": m.start(), "end": m.end()}
            )
    titles.sort(key=lambda x: x["start"])
    return titles


def _split_paragraphs(text: str) -> list[str]:
    """按空行切分段落，返回非空段落列表。"""
    return [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]


def _smart_split(text: str, limit: int, overlap: int) -> list[str]:
    """超长段落智能断句：在 limit 范围内找最近的句号/问号/感叹号/回车作为断点。

    断点优先级：中文/英文句号 > 问号/感叹号 > 换行。找不到则硬切。
    相邻块保留 overlap 字符重叠。"""
    if limit <= 0:
        return [text]
    if overlap >= limit:
        overlap = limit - 1
    if overlap < 0:
        overlap = 0
    if len(text) <= limit:
        return [text]
    # 优先级从高到低
    split_chars = ("。", ".", "!", "?", "\n")
    result: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        remaining = n - start
        if remaining <= limit:
            tail = text[start:].strip()
            if tail:
                result.append(tail)
            break
        end = start + limit
        best = end
        # 从后往前找最佳断点，至少保留 limit//2 内容
        for i in range(end - 1, start + limit // 2, -1):
            if text[i] in split_chars:
                best = i + 1  # 包含分隔符
                break
        chunk = text[start:best].strip()
        if chunk:
            result.append(chunk)
        new_start = best - overlap
        if new_start <= start:
            new_start = best  # 防止死循环
        start = new_start
    return result


def _build_chunks(
    sections: list[dict], size: int, overlap: int
) -> list[dict]:
    """从 sections 生成 chunks 列表。

    每个 section 是 {parent_chain: list[str], body: str}。
    body 内多段落贪心合并到 size；超长单段智能切分；每个 chunk 带 parent_chain 前缀。"""
    chunks: list[dict] = []
    for sec in sections:
        parent_chain = sec["parent_chain"]
        body = sec["body"]
        if not body.strip():
            continue
        prefix = "[" + " > ".join(parent_chain) + "]\n" if parent_chain else ""
        budget = max(1, size - len(prefix))
        blocks = _split_paragraphs(body)
        if not blocks:
            continue

        cur: list[str] = []  # 当前 buffer 里的段落
        cur_len = 0

        def emit() -> None:
            if not cur:
                return
            text_body = "\n\n".join(cur)
            content = (prefix + text_body) if prefix else text_body
            # is_code_block：块以代码围栏开头即视为代码块（块内可能含代码块+后续文本）
            is_code = any(b.lstrip().startswith("```") for b in cur)
            chunks.append(
                {
                    "content": content,
                    "title": parent_chain[-1] if parent_chain else "",
                    "parent_chain": list(parent_chain),
                    "level": len(parent_chain),
                    "is_code_block": is_code,
                }
            )
            # overlap: 把最后一段尾部作为下一块种子
            if overlap > 0 and cur:
                tail = cur[-1][-overlap:]
                cur.clear()
                cur.append(tail)
                cur_len = len(tail)
            else:
                cur.clear()
                cur_len = 0

        for blk in blocks:
            blk_len = len(blk)
            if blk_len > budget:
                # 先 flush 当前 buffer，再 smart-split 这个超长块
                emit()
                subs = _smart_split(blk, limit=budget, overlap=overlap)
                is_code = blk.lstrip().startswith("```")
                for i, sub in enumerate(subs):
                    if i == 0 and prefix:
                        content = prefix + sub
                    else:
                        content = sub
                    chunks.append(
                        {
                            "content": content,
                            "title": parent_chain[-1] if parent_chain else "",
                            "parent_chain": list(parent_chain),
                            "level": len(parent_chain),
                            "is_code_block": is_code,
                        }
                    )
                continue
            joiner = 2 if cur else 0
            if cur_len + joiner + blk_len <= budget:
                cur.append(blk)
                cur_len += joiner + blk_len
            else:
                emit()
                cur.append(blk)
                cur_len = blk_len
        emit()
    return chunks


def chunk_text(text: str, size: int = 512, overlap: int = 64) -> list[dict]:
    """Markdown 标题锚定切片 v2。

    算法（参照 MaxKB split_model.py，针对中文场景适配）：
      1. 提取 6 级 Markdown 标题，代码块内 # 掩码防误识别
      2. 按标题层级维护栈，把文本切成 sections
      3. 每个 section 内多段落贪心合并到 size 上限
      4. 超长单段智能断句（句号 > 问号/感叹号 > 换行 优先级）
      5. 相邻 chunk 通过 overlap 保留尾部重叠
      6. 第一块前拼接 parent_chain 链路前缀，提升检索召回

    返回 list[dict]，每段：
      {
        "content": str,            # 段正文（首个 chunk 含 parent_chain 前缀）
        "title": str,              # 最近一级标题
        "parent_chain": list[str], # 标题链路（root → 当前）
        "level": int,              # 标题层级（0 = 无标题）
        "is_code_block": bool,     # 是否整段都是代码块
      }
    纯函数。"""
    text = (text or "").strip()
    if not text:
        return []
    if size <= 0:
        raise ValueError("size 必须为正数")

    titles = _parse_titles(text)
    # 无标题：整文本作为无标题 section
    if not titles:
        return _build_chunks([{"parent_chain": [], "body": text}], size, overlap)

    sections: list[dict] = []
    # 第一个标题之前的内容
    if titles[0]["start"] > 0:
        pre = text[: titles[0]["start"]].strip()
        if pre:
            sections.append({"parent_chain": [], "body": pre})

    stack: list[tuple[int, str]] = []  # [(level, title_content)]
    for i, t in enumerate(titles):
        # 弹出 ≥ 当前层级的旧标题
        while stack and stack[-1][0] >= t["level"]:
            stack.pop()
        stack.append((t["level"], t["content"]))
        body_end = titles[i + 1]["start"] if i + 1 < len(titles) else len(text)
        body = text[t["end"] : body_end].strip()
        if not body:
            continue
        sections.append(
            {"parent_chain": [title for _, title in stack], "body": body}
        )
    return _build_chunks(sections, size, overlap)


def _model_name(llm) -> str | None:
    """从（可能被 InstrumentedLLM 包装的）实例上取底层模型名，取不到返回 None。"""
    inner = getattr(llm, "_inner", llm)
    return getattr(inner, "chat_model", None) or None


# ---------------------------------------------------------------------------
# 自动问题生成（PR-C，参照 MaxKB Problem 模型）
# ---------------------------------------------------------------------------

GENERATE_QUESTIONS_SYSTEM = (
    "你是知识库内容分析助手。阅读给定的知识片段，生成用户最可能问的若干问题。"
    "问题应能从该片段中找到答案，措辞自然多样。\n"
    "严格只输出 JSON 字符串数组，如 [\"问题1\", \"问题2\", \"问题3\"]，不要输出其他文字。"
)


def _parse_question_array(text: str, limit: int) -> list[str]:
    """容错提取 LLM 输出的 JSON 字符串数组。失败或空返回 []。"""
    if not text:
        return []
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
            if len(out) >= limit:
                break
    return out


async def _generate_questions_for_chunk(
    chat_llm, content: str, n: int
) -> list[str]:
    """为单个 chunk 生成最多 n 个候选问题。失败返回 []（不影响主流程）。"""
    if not content or not content.strip():
        return []
    user_msg = f"片段：\n{content[:1500]}\n\n请生成恰好 {n} 个问题。"
    try:
        raw = await chat_llm.chat(
            [
                {"role": "system", "content": GENERATE_QUESTIONS_SYSTEM},
                {"role": "user", "content": user_msg},
            ]
        )
    except Exception as exc:
        logger.warning("问题生成失败: %s", exc)
        return []
    questions = _parse_question_array(raw, limit=n)
    return questions


async def _generate_questions_for_chunks(
    tenant_id: int, chunk_contents: list[str]
) -> list[list[str]]:
    """为所有 chunk 串行生成问题（每批 1 个 chunk 调 chat 模型，避免打爆接口）。

    返回 list[list[str]]，与 chunk_contents 一一对应；任一 chunk 失败不影响其他。
    """
    if not chunk_contents:
        return []
    try:
        chat_llm = await resolve_chat_llm(caller="question_gen", tenant_id=tenant_id)
    except Exception as exc:
        logger.warning("问题生成 chat LLM 解析失败: %s", exc)
        return [[] for _ in chunk_contents]
    n = max(1, settings.RAG_QUESTIONS_PER_CHUNK)
    out: list[list[str]] = []
    for content in chunk_contents:
        qs = await _generate_questions_for_chunk(chat_llm, content, n)
        out.append(qs)
    return out


async def _vision_chat(llm, data: bytes, mime: str, prompt: str) -> str:
    """调视觉模型识别一段二进制图像（OpenAI image_url data URL 格式），返回剥离思考块的文本。"""
    b64 = base64.b64encode(data).decode()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ]
    return _strip_think(await llm.chat(messages))


def _render_pdf_pages(path: Path) -> list[bytes]:
    """pymupdf 逐页渲染为 PNG（zoom≈2）。同步 CPU 密集，调用方放线程执行。"""
    import pymupdf  # 仅在扫描件兜底路径引入

    pdf = pymupdf.open(str(path))
    try:
        if pdf.page_count > PDF_MAX_OCR_PAGES:
            raise RuntimeError(
                f"PDF 页数({pdf.page_count})超过视觉识别上限 {PDF_MAX_OCR_PAGES} 页"
            )
        return [
            page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png") for page in pdf
        ]
    finally:
        pdf.close()


def _read_pdf(path: Path) -> tuple[str, int]:
    """pypdf 提取文本，返回 (文本, 页数)。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return text, len(reader.pages)


async def _extract_audio(video_path: Path) -> Path:
    """ffmpeg 抽取视频音轨到临时 mp3，返回临时文件路径（调用方负责清理）。"""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("视频处理需要安装 ffmpeg 抽取音轨")
    fd, tmp_name = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    tmp = Path(tmp_name)
    for codec in (["-acodec", "libmp3lame"], ["-acodec", "copy"]):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(video_path), "-vn", *codec, str(tmp),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if await proc.wait() == 0 and tmp.stat().st_size > 0:
            return tmp
    tmp.unlink(missing_ok=True)
    raise RuntimeError("ffmpeg 抽取音轨失败")


def _email_header_text(subject, sender, to, date) -> str:
    lines = []
    for label, v in (("主题", subject), ("发件人", sender), ("收件人", to), ("日期", date)):
        if v:
            lines.append(f"{label}：{v}")
    return "\n".join(lines)


def _read_eml(path: Path) -> tuple[str, list[tuple[str, bytes]]]:
    """stdlib email 解析 .eml：返回 (头部+正文文本, [(附件名, 字节)])。
    正文优先 text/plain，只有 html 时剥标签兜底。纯同步，调用方放线程。"""
    import email
    from email import policy

    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    header = _email_header_text(msg.get("subject"), msg.get("from"), msg.get("to"), msg.get("date"))

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[tuple[str, bytes]] = []
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            data = part.get_payload(decode=True) or b""
            attachments.append((part.get_filename() or "未命名附件", data))
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain_parts.append(str(part.get_content()))
        elif ctype == "text/html":
            html_parts.append(str(part.get_content()))
    body = "\n".join(plain_parts) if plain_parts else html_to_text("\n".join(html_parts))
    return f"{header}\n\n{body}".strip(), attachments


def _read_msg(path: Path) -> tuple[str, list[tuple[str, bytes]]]:
    """extract_msg 解析 Outlook .msg：返回 (头部+正文文本, [(附件名, 字节)])。纯同步。"""
    import extract_msg

    m = extract_msg.Message(str(path))
    try:
        header = _email_header_text(m.subject, m.sender, m.to, m.date)
        body = m.body or ""
        if not body.strip() and m.htmlBody:
            raw = m.htmlBody
            body = html_to_text(raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw))
        attachments = []
        for a in m.attachments:
            data = getattr(a, "data", None)
            if not data:
                continue  # 嵌入邮件等无 data 的附件跳过
            attachments.append((a.longFilename or a.shortFilename or "未命名附件", data))
        return f"{header}\n\n{body}".strip(), attachments
    finally:
        m.close()


async def _extract_email_attachments(
    doc: KnowledgeDocument, attachments: list[tuple[str, bytes]], depth: int
) -> list[str]:
    """逐个解析邮件附件：写临时文件后递归走主分派（附件 pdf 扫描件也能 OCR）。
    单个附件失败/不支持不拖垮整封邮件，只标注说明。"""
    sections: list[str] = []
    for name, data in attachments:
        att_ext = Path(name).suffix.lower()
        if att_ext in EMAIL_EXTS:
            sections.append(f"【附件：{name}】（嵌套邮件不再展开）")
            continue
        if att_ext not in SUPPORTED_EXTS:
            sections.append(f"【附件：{name}】（格式不支持，未解析）")
            continue
        fd, tmp_name = tempfile.mkstemp(suffix=att_ext)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            tmp.write_bytes(data)
            sub = SimpleNamespace(
                id=doc.id, tenant_id=doc.tenant_id,
                file_path=str(tmp), file_type=att_ext.lstrip("."),
            )
            att_text, _method, _model = await _extract_text(sub, depth + 1)
            if att_text.strip():
                sections.append(f"【附件：{name}】\n{att_text.strip()}")
        except Exception as exc:
            logger.warning("邮件附件 %s 解析失败，跳过: %s", name, exc)
            sections.append(f"【附件：{name}】（解析失败：{str(exc) or repr(exc)}）")
        finally:
            tmp.unlink(missing_ok=True)
    return sections


async def _extract_text(
    doc: KnowledgeDocument, _depth: int = 0, force_vision: bool = False
) -> tuple[str, str, str | None]:
    """按文件类型分派产出文本，返回 (raw_text, processing_method, processing_model)。

    视觉/ASR 模型未配置或调用失败直接抛异常，由 process_document 统一降级为 failed。
    force_vision=True 时跳过本地 OCR 直接用视觉模型（供"视觉复核"用）。"""
    path = Path(doc.file_path)
    ext = "." + (doc.file_type or "").lower().lstrip(".")

    if ext in IMAGE_EXTS:
        # 三级递进：本地 OCR（含文字图片）→ 视觉模型（无文字图/照片/图表描述）
        data, mime = await asyncio.to_thread(_read_image_for_vision, path, ext)
        if not force_vision and settings.OCR_PROVIDER.strip().lower() != "vision":
            ocr_text = await asyncio.to_thread(ocr_image_bytes, data)
            if len(ocr_text.strip()) >= settings.OCR_IMAGE_MIN_CHARS:
                return ocr_text, "ocr", ocr_provider_name()
        llm = await resolve_vision_llm(caller="vision", tenant_id=doc.tenant_id)
        text = await _vision_chat(llm, data, mime, PROMPT_IMAGE_OCR)
        return text, "image_describe", _model_name(llm)

    if ext in AUDIO_EXTS:
        llm = await resolve_asr_llm(caller="asr", tenant_id=doc.tenant_id)
        text = await llm.transcribe(str(path))
        return text, "asr", _model_name(llm)

    if ext in VIDEO_EXTS:
        tmp = await _extract_audio(path)
        try:
            llm = await resolve_asr_llm(caller="asr", tenant_id=doc.tenant_id)
            text = await llm.transcribe(str(tmp))
        finally:
            tmp.unlink(missing_ok=True)
        return text, "video_asr", _model_name(llm)

    if ext == ".pdf":
        # 层层递进：pypdf 文本提取 →（文本过少或提取异常）→ pymupdf 渲染 + 视觉模型逐页 OCR
        try:
            raw, page_count = await asyncio.to_thread(_read_pdf, path)
        except Exception as exc:
            # 加密/损坏/非常规 PDF：pypdf 失败不直接 failed，回退视觉识别
            logger.warning("pypdf 提取失败，回退视觉识别: %s", exc)
            raw, page_count = "", 0
        # 扫描件兜底：提取文本过少 → 逐页渲染 PNG，先本地 OCR，不足再走视觉模型
        if len(raw.strip()) < (page_count or 1) * PDF_SCAN_MIN_CHARS_PER_PAGE:
            pages = await asyncio.to_thread(_render_pdf_pages, path)
            # 一级：本地 OCR（快、免费）
            if not force_vision and settings.OCR_PROVIDER.strip().lower() != "vision":
                try:
                    ocr_lines = await asyncio.to_thread(
                        lambda: [ocr_image_bytes(png) for png in pages]
                    )
                except Exception as exc:
                    logger.warning("扫描 PDF 本地 OCR 失败，回退视觉: %s", exc)
                    ocr_lines = []
                merged_ocr = "\n\n".join(t for t in ocr_lines if t and t.strip())
                if len(merged_ocr.strip()) >= (page_count or 1) * settings.OCR_PDF_MIN_CHARS_PER_PAGE:
                    return merged_ocr, "ocr", ocr_provider_name()
            # 二级：视觉模型逐页识别
            llm = await resolve_vision_llm(caller="vision", tenant_id=doc.tenant_id)
            texts = []
            for i, png in enumerate(pages):
                try:
                    texts.append(await _vision_chat(llm, png, "image/png", PROMPT_PDF_PAGE_OCR))
                except Exception as exc:
                    # 单页失败跳过继续，不拖垮整份文档
                    logger.warning("文档 %s 第 %s 页视觉识别失败，跳过: %s", doc.id, i + 1, exc)
            merged = "\n\n".join(t for t in texts if t and t.strip())
            if not merged.strip():
                raise RuntimeError("扫描版 PDF 视觉识别未产出文本（请检查视觉/对话模型是否支持图片输入）")
            return merged, "vision_ocr", _model_name(llm)
        return raw, "text", None

    if ext in EMAIL_EXTS:
        if _depth >= 1:
            raise RuntimeError("嵌套邮件不再展开")
        if ext == ".eml":
            body, attachments = await asyncio.to_thread(_read_eml, path)
        else:
            body, attachments = await asyncio.to_thread(_read_msg, path)
        sections = [body] if body.strip() else []
        sections.extend(await _extract_email_attachments(doc, attachments, _depth))
        if not sections:
            raise RuntimeError("邮件无正文与可解析附件")
        return "\n\n".join(sections), "email", None

    raw = await asyncio.to_thread(parse_file, path, doc.file_type)
    return raw, "text", None


async def _persist_parsed_document(
    session, doc: KnowledgeDocument, raw: str, method: str, model: str | None
) -> None:
    """把解析出的文本写入文档：清洗→切片→批量 embed→写 chunks→更新状态/元数据（含版本快照）。

    process_document 与 vision_review_document 共用；旧切片由调用方先清理。
    失败抛异常，由调用方统一处理状态。"""
    text = clean_text(raw)
    chunks = chunk_text(text, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
    if not chunks:
        raise ValueError("文档内容为空，无法切片")

    # chunk_text v2 返回 list[dict]，仅取 content 做向量化
    chunk_contents = [c["content"] for c in chunks]

    try:
        embed_llm = await resolve_embed_llm(caller="embed", tenant_id=doc.tenant_id)
        # 分批嵌入（每批 32），单批失败重试一次，避免一次请求过大
        embeddings: list[list[float]] = []
        for i in range(0, len(chunk_contents), 32):
            batch = chunk_contents[i : i + 32]
            try:
                embeddings.extend(await embed_llm.embed(batch))
            except Exception:
                embeddings.extend(await embed_llm.embed(batch))
    except Exception as exc:
        raise RuntimeError(f"嵌入模型调用失败: {exc}") from exc
    if len(embeddings) != len(chunks):
        raise RuntimeError(
            f"嵌入返回数量({len(embeddings)})与切片数量({len(chunks)})不一致"
        )

    chunk_objects: list[tuple[DocumentChunk, str]] = []
    for i, (content, emb) in enumerate(zip(chunk_contents, embeddings)):
        chunk = DocumentChunk(
            document_id=doc.id,
            tenant_id=doc.tenant_id,
            chunk_index=i,
            content=content,
            embedding=emb,
        )
        session.add(chunk)
        chunk_objects.append((chunk, content))
    # blend 检索：为新 chunk 构造 tsvector（simple 配置：空格分词 + 中文逐字）。
    # 先 flush 让 INSERT 落地，UPDATE 才能命中新行；UPDATE 后再 flush 让 chunk.id 可用。
    await session.flush()
    await session.execute(
        sql_text(
            "UPDATE document_chunks SET search_vector = to_tsvector('simple', content)"
            " WHERE document_id = :did AND search_vector IS NULL"
        ),
        {"did": doc.id},
    )
    await session.flush()
    # 自动问题生成（PR-C）：为每个 chunk 用 LLM 生成候选问题，提升召回
    questions_per_chunk: list[list[str]] = [[] for _ in chunk_contents]
    if settings.RAG_GENERATE_QUESTIONS and chunk_objects:
        try:
            questions_per_chunk = await _generate_questions_for_chunks(
                doc.tenant_id, chunk_contents
            )
        except Exception as exc:
            logger.warning("文档 %s 问题生成失败（跳过）: %s", doc.id, exc)
        flat_questions: list[tuple[int, str]] = []
        for (chunk, _), qs in zip(chunk_objects, questions_per_chunk):
            for q in qs:
                flat_questions.append((chunk.id, q))
        if flat_questions:
            try:
                q_embeddings = await embed_llm.embed([q for _, q in flat_questions])
            except Exception as exc:
                logger.warning("问题 embed 失败（跳过入库）: %s", exc)
                q_embeddings = []
            for (chunk_id, q), emb in zip(flat_questions, q_embeddings):
                session.add(
                    ChunkQuestion(
                        chunk_id=chunk_id,
                        tenant_id=doc.tenant_id,
                        content=q,
                        embedding=emb,
                    )
                )
    doc.status = "ready"
    doc.chunk_count = len(chunks)
    # 版本历史：重解析前保存旧内容快照（首次解析无旧内容则跳过）
    if doc.content and doc.content.strip() and not doc.content.startswith("[处理失败]"):
        from app.models.document_version import DocumentVersion

        session.add(
            DocumentVersion(
                document_id=doc.id,
                content=doc.content,
                chunk_count=doc.chunk_count or 0,
                note="重解析前快照",
            )
        )
    doc.content = (_CONTENT_PREFIX.get(method, "") + text)[:10000]
    meta = dict(doc.doc_metadata or {})
    meta["processing_method"] = method
    if model:
        meta["processing_model"] = model
    if settings.RAG_GENERATE_QUESTIONS:
        # 合并问题生成元数据（PR-C）：记录入库数量与状态供前端展示
        meta["question_gen_count"] = sum(
            len(qs) for qs in questions_per_chunk
        )
        meta["question_gen_status"] = (
            "ok" if any(questions_per_chunk) else "empty"
        )
    doc.doc_metadata = meta


async def process_document(doc_id: int) -> None:
    """读文件→（多模态）解析出文本→清洗→切片（v2 标题锚定）→批量 embed→写 chunks→更新文档状态。
    任何失败只把文档标记为 failed，绝不抛出。"""
    async with AsyncSessionLocal() as session:
        doc = await session.get(KnowledgeDocument, doc_id)
        if doc is None:
            logger.warning("process_document: 文档 %s 不存在", doc_id)
            return
        # PST 归档：委托给流式拆解管线（逐封邮件拆成独立文档再各自走本管线）
        if (doc.file_type or "").lower() == "pst":
            from app.services.pst import process_pst

            await process_pst(doc_id)
            return
        try:
            # 幂等重解析：先清旧切片并立即提交。
            # 关键：不能把 DELETE 与后面耗时的 LLM 调用放同一事务——否则 LLM 一慢，
            # 事务就长期持有 document_chunks 的锁（会堵死 REINDEX CONCURRENTLY 等维护）。
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == doc.id)
            )
            await session.commit()
            raw, method, model = await _extract_text(doc)
            await _persist_parsed_document(session, doc, raw, method, model)
        except Exception as exc:
            logger.exception("文档 %s 处理失败", doc_id)
            msg = str(exc) or repr(exc)  # 部分异常 str 为空（如裸 PdfReadError），兜底 repr
            doc.status = "failed"
            doc.content = f"[处理失败] {msg}"
            meta = dict(doc.doc_metadata or {})
            meta["error"] = msg
            doc.doc_metadata = meta
            from app.services.error_log import log_error

            await log_error("error", "ingestion", f"文档 {doc_id} 处理失败", msg, tenant_id=doc.tenant_id)
        await session.commit()


async def vision_review_document(doc_id: int) -> None:
    """视觉复核：对本地 OCR 识别质量差的图片/扫描 PDF，用视觉模型重新转录并更新切片。

    仅允许 status=ready 且属于视觉可识别（图片/扫描 PDF 的结果方法）的文档；
    视觉识别成功前不清旧切片，失败时保留旧内容可读（只记 vision_review_error 元数据），
    绝不把文档打成 failed。可反复复核。"""
    async with AsyncSessionLocal() as session:
        doc = await session.get(KnowledgeDocument, doc_id)
        if doc is None:
            logger.warning("vision_review_document: 文档 %s 不存在", doc_id)
            return
        try:
            cur_method = (doc.doc_metadata or {}).get("processing_method")
            if doc.status != "ready":
                raise ValueError("仅就绪文档可视觉复核")
            if cur_method not in ("ocr", "image_describe", "vision_ocr"):
                raise ValueError("仅图片/扫描 PDF（视觉可识别）文档可视觉复核")
            # 先做耗时的视觉提取（成功后再清旧切片，失败保留旧内容）
            raw, method, model = await _extract_text(doc, force_vision=True)
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == doc.id)
            )
            await session.commit()
            await _persist_parsed_document(session, doc, raw, method, model)
            meta = dict(doc.doc_metadata or {})
            meta["vision_reviewed_at"] = datetime.now(timezone.utc).isoformat()
            meta.pop("vision_review_error", None)
            doc.doc_metadata = meta
            logger.info("文档 %s 视觉复核完成（%s，%s 个切片）", doc_id, method, doc.chunk_count)
        except Exception as exc:
            logger.warning("文档 %s 视觉复核失败（保留旧内容）: %s", doc_id, exc)
            meta = dict(doc.doc_metadata or {})
            meta["vision_review_error"] = str(exc)[:300]
            doc.doc_metadata = meta
            from app.services.error_log import log_error

            await log_error("warning", "ingestion", f"文档 {doc_id} 视觉复核失败", str(exc), tenant_id=doc.tenant_id)
        await session.commit()


async def recover_processing_documents() -> list[int]:
    """启动恢复，返回需重新排队的文档 id：
    1. 滞留 processing 的文档（进程重启会杀掉在飞的后台任务）；
    2. status=unsupported 但当前规则已支持的文档（格式清单升级，如新增 html）→ 重置为 processing。
    DB 不可用时返回空表，不影响启动。"""
    try:
        async with AsyncSessionLocal() as session:
            docs = (
                await session.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.status.in_(["processing", "unsupported"])
                    )
                )
            ).scalars().all()
            ids: list[int] = []
            for doc in docs:
                if doc.status == "unsupported":
                    if not is_supported(doc.file_name):
                        continue
                    doc.status = "processing"
                ids.append(doc.id)
            await session.commit()
            return ids
    except Exception as exc:
        logger.warning("启动恢复扫描失败: %s", exc)
        return []


async def rescan_supported_files() -> int:
    """支持格式清单升级后，回填存量文件的 supported 标记（如新增 html 解析支持）。
    返回修正行数；DB 不可用返回 0。"""
    from app.models.library_file import LibraryFile

    try:
        async with AsyncSessionLocal() as session:
            files = (
                await session.execute(
                    select(LibraryFile).where(LibraryFile.deleted_at.is_(None))
                )
            ).scalars().all()
            changed = 0
            for f in files:
                now = is_supported(f.file_name)
                if f.supported != now:
                    f.supported = now
                    changed += 1
            await session.commit()
            return changed
    except Exception as exc:
        logger.warning("supported 标记回填失败: %s", exc)
        return 0
