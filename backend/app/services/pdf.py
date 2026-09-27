"""HTML 报告 → A4 PDF 渲染（Playwright + Chromium，服务端打印）。

Playwright 为可选依赖：未安装或浏览器二进制缺失时抛 RuntimeError，
由接口层转 503。sync_playwright 必须在 asyncio.to_thread 中调用
（FastAPI 事件循环内不能直接用 sync API）。"""
import logging

import app.config  # noqa: F401  导入即设置 PLAYWRIGHT_BROWSERS_PATH（须在 playwright 启动前生效）

logger = logging.getLogger(__name__)

INSTALL_HINT = "PDF 导出未安装：pip install playwright && python -m playwright install chromium"

# A4 分页优化 CSS：页边距、标题后不分页、表格/图片/代码块不跨页断裂
_PRINT_CSS = """
@page { size: A4; margin: 18mm 16mm; }
h1, h2, h3 { page-break-after: avoid; }
table, figure, img, pre, blockquote { page-break-inside: avoid; }
tr { page-break-inside: avoid; }
"""

# 演示版（16:9 幻灯片）PDF 分页 CSS：一页一幻灯片、无页边距，页面横向 16:9
_PRES_PRINT_CSS = """
@page { size: 13.333in 7.5in; margin: 0; }
html, body { margin: 0; padding: 0; }
.slide { page-break-after: always; break-after: page; }
.slide:last-child { page-break-after: auto; break-after: auto; }
"""


def render_pdf_from_html(html: str) -> bytes:
    """把完整 HTML 文档渲染为 A4 PDF 字节。同步阻塞，调用方须放线程执行。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(INSTALL_HINT) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="networkidle")
                page.add_style_tag(content=_PRINT_CSS)
                return page.pdf(
                    format="A4", print_background=True, prefer_css_page_size=True
                )
            finally:
                browser.close()
    except RuntimeError:
        raise
    except Exception as exc:
        # 浏览器二进制缺失等启动/渲染失败
        logger.warning("PDF 渲染失败: %s", exc)
        raise RuntimeError(INSTALL_HINT) from exc


def render_presentation_pdf(html: str) -> bytes:
    """把演示版 HTML（16:9 幻灯片）渲染为横向 PDF，一页一幻灯片。同步阻塞，须放线程执行。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(INSTALL_HINT) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.add_style_tag(content=_PRES_PRINT_CSS)
                return page.pdf(
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                )
            finally:
                browser.close()
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("演示 PDF 渲染失败: %s", exc)
        raise RuntimeError(INSTALL_HINT) from exc
