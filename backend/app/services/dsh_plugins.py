"""dsh 插件管理（管理端统一插件库，全租户生效）。

插件树来源：`dsh --profile acp --dump-config`（组合后的 profile 树，含 profile
自带 patch 层，不含 --patch 覆盖层）。管理员的启停覆盖写入
patches/plugins.yml——它作为 --patch 叠加层在 base.yml / acp-model.yml 之后
加载，优先级最高；改完重启 dsh ACP 进程生效（会话历史在 PG，重启不丢上下文，
下次 agent 请求懒重建并 resume）。

安装新插件：`dsh plugin --profile acp add <spec>`（原样转发 pnpm，需要 pnpm
在 PATH），装好后在 plugins.yml 追加 insert 条目使其加载；insert 条目的插件
不出现在 dump 树里，列表从覆盖层读取展示。

Windows 下 DSH_BIN 是 .cmd shim，必须经 cmd /c 调用；子进程输出一律按 UTF-8
解码（dsh 输出含非 GBK 字符，text=True 默认走本地编码会炸）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import yaml

from app.config import settings

logger = logging.getLogger(__name__)


class DshPluginError(Exception):
    """dsh 插件操作失败（dump 失败、安装失败等），detail 可直接回前端。"""


# ---------------------------------------------------------------------------
# YAML：dsh 配置含 !!js 表达式（如 disabled: !!js '...'），解析成标记 dict 保留原文
# ---------------------------------------------------------------------------

class _DshLoader(yaml.SafeLoader):
    pass


def _construct_js(loader, suffix, node):
    return {"__js__": loader.construct_scalar(node)}


_DshLoader.add_multi_constructor("tag:yaml.org,2002:js", _construct_js)


def _patches_dir() -> Path:
    d = settings.dsh_patches_path
    d.mkdir(parents=True, exist_ok=True)
    return d


def _overrides_path() -> Path:
    return _patches_dir() / "plugins.yml"


# ---------------------------------------------------------------------------
# 子进程执行（Windows .cmd 经 cmd /c；输出按 UTF-8 解码）
# ---------------------------------------------------------------------------

def _dsh_bin() -> str:
    if not settings.DSH_BIN:
        raise DshPluginError("未配置 DSH_BIN（Windows 指 dsh.cmd shim，Linux 指无后缀 shim）")
    return settings.DSH_BIN


def _wrap_cmd(args: list[str]) -> list[str]:
    """Windows 下 .cmd shim 必须经 cmd /c 调用（不能直接 exec）。"""
    if sys.platform == "win32" and args[0].lower().endswith(".cmd"):
        return ["cmd", "/c", *args]
    return args


async def _run_dsh(args: list[str], timeout: int) -> tuple[int, str]:
    """跑 dsh CLI，返回 (returncode, 输出文本)。输出合流 stderr，UTF-8 容错解码。"""
    cmd = _wrap_cmd([_dsh_bin(), *args])
    env = dict(os.environ, DSH_HOME=str(settings.dsh_home_path))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise DshPluginError(f"dsh 命令超时（{timeout}s）")
    text = (out or b"").decode("utf-8", errors="replace")
    return proc.returncode or 0, text


# ---------------------------------------------------------------------------
# 插件树 dump 解析
# ---------------------------------------------------------------------------

def parse_dump(text: str) -> list[dict]:
    """解析 `dsh --profile acp --dump-config` 输出为插件条目列表。

    返回 [{id, name, bundle, disabled, disabled_expr, has_config}]：
    - bundle：行级扫描 `# == <bundle>` 注释归属（yaml 解析丢注释，单独扫）
    - disabled：True/False；!!js 表达式无法静态求值，记入 disabled_expr
    """
    entries = yaml.load(text, Loader=_DshLoader)
    if not isinstance(entries, list):
        return []
    # 行级扫描：每条顶层 "- id:" 归属到前方最近的 "# == <bundle>" 注释
    bundles: list[str] = []
    current = ""
    for line in text.splitlines():
        if line.startswith("# == "):
            current = line[5:].strip()
        elif line.startswith("- id: "):
            bundles.append(current)
    items = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not e.get("id"):
            continue
        raw = e.get("disabled")
        disabled: bool | None = None
        expr = None
        if isinstance(raw, bool):
            disabled = raw
        elif isinstance(raw, dict) and "__js__" in raw:
            expr = raw["__js__"]
        else:
            # 未声明 disabled = 启用（dsh 默认行为）
            disabled = False
        items.append({
            "id": e["id"],
            "name": e.get("name"),
            "bundle": bundles[i] if i < len(bundles) else "",
            "disabled": disabled,
            "disabled_expr": expr,
            "has_config": "config" in e,
        })
    return items


async def dump_tree() -> list[dict]:
    rc, text = await _run_dsh(["--profile", "acp", "--dump-config"], timeout=120)
    if rc != 0:
        raise DshPluginError(f"dsh dump-config 失败（rc={rc}）：{text[-500:]}")
    return parse_dump(text)


# ---------------------------------------------------------------------------
# 管理员覆盖层 plugins.yml
# ---------------------------------------------------------------------------

def load_overrides() -> list[dict]:
    p = _overrides_path()
    if not p.exists():
        return []
    try:
        data = yaml.load(p.read_text(encoding="utf-8"), Loader=_DshLoader)
    except yaml.YAMLError as exc:
        logger.warning("plugins.yml 解析失败（按空覆盖处理）: %s", exc)
        return []
    return data if isinstance(data, list) else []


def save_overrides(entries: list[dict]) -> Path:
    p = _overrides_path()
    body = yaml.safe_dump(entries, allow_unicode=True, sort_keys=False)
    header = (
        "# dsh 插件管理员覆盖层（管理端「dsh 插件」页维护，请勿手改）。\n"
        "# 作为 --patch 在 base.yml / acp-model.yml 之后加载，同 id 条目以此为准；\n"
        "# 改动在 dsh ACP 进程重建后生效（管理端保存时已自动重建，会话历史在 PG 不丢）。\n"
    )
    tmp = p.with_suffix(".yml.tmp")
    tmp.write_text(header + body, encoding="utf-8")
    tmp.replace(p)
    return p


def override_disabled_map() -> dict[str, bool]:
    """覆盖层中按 id 的 disabled 设置（insert 条目除外）。"""
    out: dict[str, bool] = {}
    for e in load_overrides():
        if isinstance(e, dict) and "id" in e and isinstance(e.get("disabled"), bool):
            out[e["id"]] = e["disabled"]
    return out


def process_patch_disabled_map() -> dict[str, bool]:
    """进程级 patch（acp-model.yml，自动生成）里的静态 disabled 设置。

    dump-config 不含 --patch 覆盖层，但它比 plugins.yml 优先级低、比 profile 树高，
    展示有效状态时作为中间层合并（否则如 tool-web 会错误显示为"启用中"）。
    """
    p = _patches_dir() / "acp-model.yml"
    if not p.exists():
        return {}
    try:
        data = yaml.load(p.read_text(encoding="utf-8"), Loader=_DshLoader)
    except yaml.YAMLError:
        return {}
    out: dict[str, bool] = {}
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and "id" in e and isinstance(e.get("disabled"), bool):
            out[e["id"]] = e["disabled"]
    return out


def set_plugin_enabled(plugin_id: str, enabled: bool) -> None:
    """启停插件：覆盖层同 id 条目更新 disabled，没有则追加。"""
    entries = load_overrides()
    target = not enabled
    for e in entries:
        if isinstance(e, dict) and e.get("id") == plugin_id and "disabled" in e:
            e["disabled"] = target
            break
    else:
        entries.append({"id": plugin_id, "disabled": target})
    save_overrides(entries)


def custom_insert_entries() -> list[dict]:
    """覆盖层中 insert 进来的自定义插件（管理端安装的）。"""
    out = []
    for e in load_overrides():
        if isinstance(e, dict) and isinstance(e.get("insert"), list):
            for sub in e["insert"]:
                if isinstance(sub, dict) and sub.get("id"):
                    out.append({"id": sub["id"], "name": sub.get("name")})
    return out


def remove_custom_entry(entry_id: str) -> bool:
    """移除 insert 的自定义插件（停用即移除 insert 条目；已安装包保留在 profile）。"""
    entries = load_overrides()
    removed = False
    new_entries = []
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("insert"), list):
            kept = [s for s in e["insert"] if not (isinstance(s, dict) and s.get("id") == entry_id)]
            removed = removed or len(kept) != len(e["insert"])
            if kept:
                new_entries.append({"insert": kept})
            continue
        new_entries.append(e)
    if removed:
        save_overrides(new_entries)
    return removed


# ---------------------------------------------------------------------------
# 合并视图（dump 树 + 覆盖层）
# ---------------------------------------------------------------------------

async def list_plugins() -> dict:
    """管理端插件清单：dsh 树 + 进程级 patch + 管理员覆盖层合并后的有效状态。

    优先级（高 → 低）：plugins.yml（管理员覆盖）> acp-model.yml（进程级生成）> profile 树。
    """
    tree = await dump_tree()
    overrides = override_disabled_map()
    process_patch = process_patch_disabled_map()
    items = []
    seen = set()
    for it in tree:
        seen.add(it["id"])
        overridden = it["id"] in overrides
        # 有效状态：管理员覆盖层 > 进程级 patch > 树内静态布尔 > 动态表达式（None 随上下文求值）
        if overridden:
            effective_disabled: bool | None = overrides[it["id"]]
        elif it["id"] in process_patch:
            effective_disabled = process_patch[it["id"]]
        else:
            effective_disabled = it["disabled"]
        items.append({
            **it,
            "effective_disabled": effective_disabled,
            "overridden": overridden,
            "custom": False,
        })
    for c in custom_insert_entries():
        if c["id"] in seen:
            continue
        items.append({
            "id": c["id"],
            "name": c.get("name"),
            "bundle": "plugins.yml（管理员安装）",
            "disabled": False,
            "disabled_expr": None,
            "has_config": False,
            "effective_disabled": False,
            "overridden": False,
            "custom": True,
        })
    return {"items": items}


# ---------------------------------------------------------------------------
# 安装新插件
# ---------------------------------------------------------------------------

# npm 包名 spec：[@scope/]name[@version|@tag]；或绝对路径（link 安装）
_NPM_SPEC_RE = re.compile(r"^(@[a-z0-9._-]+/)?[a-z0-9._-]+(@[a-z0-9.+\-_/]+)?$", re.IGNORECASE)


def _validate_spec(spec: str) -> str:
    spec = spec.strip()
    if not spec:
        raise DshPluginError("包名不能为空")
    if spec.startswith("-"):
        raise DshPluginError("非法包名（不得以 - 开头，防选项注入）")
    if _NPM_SPEC_RE.match(spec):
        return spec
    p = Path(spec)
    if p.is_absolute() and p.exists():
        return spec
    raise DshPluginError("包名格式不正确（npm 包名 [@scope/]name[@version] 或本地绝对路径）")


def _spec_package_name(spec: str) -> str:
    """从 spec 提取包名（@scope/name@version → @scope/name）；本地路径读 package.json。"""
    p = Path(spec)
    if p.is_absolute() and (p / "package.json").exists():
        import json

        try:
            return json.loads((p / "package.json").read_text(encoding="utf-8"))["name"]
        except Exception:
            return p.name
    # npm spec：@scope/name@x 去掉最后一个 @ 之后；name@x → name
    if spec.startswith("@"):
        head, _, _ver = spec.rpartition("@")
        return head or spec
    return spec.split("@", 1)[0]


async def install_plugin(spec: str) -> dict:
    """安装 dsh 插件包并在覆盖层 insert 加载。

    dsh plugin add 只做 pnpm add（装包），插件条目要在 patch 层 insert 才挂载——
    与 PG 会话持久化插件的安装方式一致。
    """
    spec = _validate_spec(spec)
    rc, text = await _run_dsh(["plugin", "--profile", "acp", "add", spec], timeout=600)
    if rc != 0:
        raise DshPluginError(f"插件安装失败（rc={rc}）：{text[-800:]}")
    pkg = _spec_package_name(spec)
    entry_id = pkg.rsplit("/", 1)[-1]
    entries = load_overrides()
    # 幂等：已 insert 过同 id 则不重复追加
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("insert"), list):
            for sub in e["insert"]:
                if isinstance(sub, dict) and sub.get("id") == entry_id:
                    return {"package": pkg, "id": entry_id, "already": True, "output": text[-2000:]}
    entries.append({"insert": [{"id": entry_id, "name": pkg}]})
    save_overrides(entries)
    return {"package": pkg, "id": entry_id, "already": False, "output": text[-2000:]}
