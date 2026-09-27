"""自定义 API skill：管理端配置 method/url/headers/body + 参数 Schema，
url 与 body 支持 {{arg}} 占位符替换。"""
import json
import re
from urllib.parse import urlparse

import httpx

from app.services.skills.base import Skill, truncate_result
from app.services.skills.builtin import check_url_safe

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_template(template: str, args: dict) -> str:
    """{{arg}} 占位符替换；未提供的参数保留原样。纯函数。"""
    return _PLACEHOLDER.sub(lambda m: str(args.get(m.group(1), m.group(0))), template or "")


class ApiSkill(Skill):
    """管理端自定义的 HTTP API 工具。"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict | None,
        config: dict | None,
        display_name: str | None = None,
    ):
        super().__init__(config)
        self.name = name
        self.description = description or display_name or name
        self.parameters = parameters or {"type": "object", "properties": {}}

    async def run(self, args: dict, ctx: dict) -> str:
        cfg = self.config
        url = render_template(cfg.get("url", ""), args)
        await check_url_safe(url)
        method = (cfg.get("method") or "GET").upper()
        if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            raise ValueError(f"不支持的请求方法: {method}")
        headers = dict(cfg.get("headers") or {})
        body_tpl = cfg.get("body")
        kwargs: dict = {"headers": headers}
        if method == "GET":
            kwargs["params"] = {k: str(v) for k, v in args.items()}
        elif body_tpl:
            rendered = render_template(body_tpl, args)
            try:
                kwargs["json"] = json.loads(rendered)
            except json.JSONDecodeError:
                kwargs["content"] = rendered
        else:
            kwargs["json"] = args

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
            try:
                data = resp.json()
                text = json.dumps(data, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                text = resp.text
        return truncate_result(text or "（空响应）")
