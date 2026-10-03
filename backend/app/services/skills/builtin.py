"""内置 skills：web_search（tavily/bing/duckduckgo）与 web_fetch（网页抓取）。"""
import asyncio
import ipaddress
import re
import socket
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx

from app.services.skills.base import Skill, truncate_result


def _is_private(addr: ipaddress._BaseAddress) -> bool:
    """是否内网/本机/保留地址（IPv4-mapped IPv6 会被 ipaddress 归一化判定）。"""
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


async def check_url_safe(url: str) -> None:
    """校验目标 URL 可访问性（http/https + 解析后非内网）。

    字符串比对可被 IPv4-mapped IPv6 / DNS rebinding / 非十进制 IP 绕过，
    这里对显式 IP 用 ipaddress 归一化判定，对域名做 DNS 解析后逐 IP 校验。"""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅允许 http/https 链接")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("无效的 URL")

    # 显式 IP 字面量：归一化判断（含 IPv4-mapped IPv6）
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is not None:
        if _is_private(addr):
            raise ValueError("不允许访问内网/本机地址")
        return

    # 域名：解析出全部地址，任一解析到内网即拒绝（防 DNS rebinding）
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, None, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror:
        # 域名当前无法解析：无法作为 SSRF 目标（实际请求也会失败），放行交给请求本身报错
        return
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_private(ip):
            raise ValueError("不允许访问内网/本机地址")


# 重定向状态码集合
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
# 跨主机重定向时必须剥掉的敏感自定义头（防凭据泄露给重定向目标）
_SENSITIVE_HEADERS = {"authorization", "proxy-authorization", "cookie"}


async def checked_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_redirects: int = 5,
    **kwargs,
) -> httpx.Response:
    """手动跟随重定向的 SSRF 安全请求。

    httpx 的 follow_redirects=True 不会校验重定向目标（可 302 跳到内网），
    这里要求 client 以 follow_redirects=False 创建，逐跳处理：
    每一跳前重新跑 check_url_safe；跨主机重定向时剥掉 Authorization 等敏感头。
    web_fetch 与 ApiSkill 共用此函数，保持一致行为。"""
    current = url
    for _ in range(max_redirects + 1):
        await check_url_safe(current)
        resp = await client.request(method, current, **kwargs)
        if resp.status_code not in _REDIRECT_STATUSES:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        nxt = urljoin(current, location)
        # 浏览器语义：303（及带 body 的 301/302）重定向后转 GET 并丢弃请求体
        if resp.status_code == 303 or (
            resp.status_code in (301, 302) and method.upper() not in ("GET", "HEAD")
        ):
            method = "GET"
            for k in ("content", "json", "data", "files"):
                kwargs.pop(k, None)
        # 跨主机重定向：剥掉敏感自定义头
        old_host = (urlparse(current).hostname or "").lower()
        new_host = (urlparse(nxt).hostname or "").lower()
        if new_host != old_host and "headers" in kwargs:
            kwargs["headers"] = {
                k: v for k, v in dict(kwargs["headers"]).items()
                if k.lower() not in _SENSITIVE_HEADERS
            }
        current = nxt
    raise ValueError(f"重定向次数超过上限（{max_redirects}）")


class WebSearchSkill(Skill):
    """联网搜索，统一输出 Top5 标题+摘要+链接。duckduckgo 为实验性免 key 通道。"""

    name = "web_search"
    description = (
        "联网搜索最新信息，返回 Top8 结果（标题+摘要+链接）。"
        "当知识库内容不足或需要最新资讯/新闻/实时数据时使用。"
        "搜索技巧：用简洁具体的关键词（如「2026年8月 重大新闻」或具体事件名），"
        "不要堆砌多个泛化短语；新闻类需求直接搜「YYYY年M月 新闻」格式。"
        "结果不理想时换关键词重试，必要时用 web_fetch 打开相关链接阅读全文后再回答。"
    )
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "搜索关键词"}},
        "required": ["query"],
    }

    async def run(self, args: dict, ctx: dict) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            raise ValueError("缺少搜索关键词 query")
        provider = (self.config.get("provider") or "duckduckgo").lower()
        if provider == "tavily":
            results = await self._tavily(query)
        elif provider == "bing":
            results = await self._bing(query)
        elif provider == "bocha":
            results = await self._bocha(query)
        elif provider == "bing_cn":
            # 国内可达、免 key：cn.bing.com HTML 抓取（duckduckgo 被墙时推荐）
            results = await self._bing_cn(query)
        else:
            results = await self._duckduckgo(query)
        if not results:
            return "未搜索到相关结果"
        lines = []
        for i, r in enumerate(results[:5], 1):
            lines.append(f"{i}. {r['title']}\n{r['snippet']}\n{r['url']}")
        # 引用指引放在截断之后，保证模型一定能看到：
        # 模型按 [序号](URL) 输出 Markdown 链接，前端渲染为上标、悬停显示原始网址
        return truncate_result("\n\n".join(lines)) + (
            "\n\n引用规范：回答中使用以上结果时，在相关句末以 Markdown 链接标注来源序号，"
            "格式为 [序号](对应URL)，例如 [1](https://example.com)。"
        )

    async def _tavily(self, query: str) -> list[dict]:
        api_key = self.config.get("api_key")
        if not api_key:
            raise RuntimeError("web_search 未配置 tavily api_key")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": 5},
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            {"title": r.get("title", ""), "snippet": r.get("content", ""), "url": r.get("url", "")}
            for r in data.get("results", [])
        ]

    async def _bing(self, query: str) -> list[dict]:
        api_key = self.config.get("api_key")
        if not api_key:
            raise RuntimeError("web_search 未配置 bing api_key")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                "https://api.bing.microsoft.com/v7.0/search",
                params={"q": query, "count": 5},
                headers={"Ocp-Apim-Subscription-Key": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            {"title": r.get("name", ""), "snippet": r.get("snippet", ""), "url": r.get("url", "")}
            for r in data.get("webPages", {}).get("value", [])
        ]

    async def _bocha(self, query: str) -> list[dict]:
        """博查（国内 AI 搜索 API，网络可达性好）：data.webPages.value 取 Top 结果。"""
        api_key = self.config.get("api_key")
        if not api_key:
            raise RuntimeError("web_search 未配置 bocha api_key")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                "https://api.bochaai.com/v1/web-search",
                json={"query": query, "summary": True, "count": 5},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
        return [
            {
                "title": r.get("name", ""),
                "snippet": r.get("snippet") or r.get("summary", ""),
                "url": r.get("url", ""),
            }
            for r in pages
        ]

    async def _bing_cn(self, query: str) -> list[dict]:
        """国内可达、免 key：抓取 cn.bing.com 搜索结果页解析（bing 被墙/不确定时用 cn 节点）。

        返回最多 8 条；标题/摘要剥离 HTML 标签并反转义实体（&ensp; 等）。
        """
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(
                "https://cn.bing.com/search",
                params={"q": query, "setlang": "zh-hans", "count": "10"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
            )
            resp.raise_for_status()
            html = resp.text

        def _clean(s: str) -> str:
            return unescape(re.sub(r"<[^>]+>", "", s or "")).strip()

        results = []
        for block in re.findall(r'<li class="b_algo".*?</li>', html, re.S)[:8]:
            m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>', block, re.S)
            if not m:
                continue
            url = m.group(1)
            title = _clean(m.group(2))
            if not (url and title):
                continue
            p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
            snippet = _clean(p.group(1)) if p else ""
            results.append({"title": title, "snippet": snippet, "url": url})
        return results

    async def _duckduckgo(self, query: str) -> list[dict]:
        """实验性：抓取 duckduckgo html 结果页正则解析，免 key 但不稳定。"""
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            html = resp.text
        titles = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
        results = []
        for i, (url, title) in enumerate(titles[:5]):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]) if i < len(snippets) else ""
            results.append(
                {
                    "title": re.sub(r"<[^>]+>", "", title).strip(),
                    "snippet": snippet.strip(),
                    "url": url,
                }
            )
        return results


class WebFetchSkill(Skill):
    """抓取网页正文：HTML 标签剥离成纯文本截断返回。"""

    name = "web_fetch"
    description = "抓取指定 URL 的网页内容（转为纯文本）。当需要阅读某个网页全文时使用。"
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "要抓取的网页 URL"}},
        "required": ["url"],
    }

    async def run(self, args: dict, ctx: dict) -> str:
        url = (args.get("url") or "").strip()
        # follow_redirects=False：重定向由 checked_request 逐跳校验目标（防 302 跳内网）
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            resp = await checked_request(client, "GET", url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            html = resp.text
        # 去脚本/样式后剥离标签，合并空白
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return "页面无可见文本内容"
        return truncate_result(text)


# 代码默认注册（DB 无行时以默认配置出现：disabled）
BUILTIN_SKILLS: dict[str, type[Skill]] = {
    "web_search": WebSearchSkill,
    "web_fetch": WebFetchSkill,
}
