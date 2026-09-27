"""应用图标：品牌蓝圆角方块 + 白云 + 同步双箭头（分层渲染，支持旋转动画帧）。

- ensure_icons：assets/icon.png（托盘/面板）与 icon.ico（打包）静态图标
- render_frames：同步中托盘动画帧（箭头绕中心旋转，12 帧平滑转圈）
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

# 品牌蓝（与前端 --el-color-primary 默认一致）
BLUE_TOP = (59, 130, 246)     # #3b82f6
BLUE_BOTTOM = (29, 78, 216)   # #1d4ed8
WHITE = (255, 255, 255)

# 箭头中心与半径（百分比坐标，与 _sync_arrows 保持一致）
_CX, _CY, _R = 50.0, 52.0, 15.0


def _lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _rounded_gradient(size: int, radius: int) -> Image.Image:
    """对角渐变圆角方块（先画 4x 再降采样抗锯齿）。"""
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    grad = Image.new("RGBA", (big, big))
    gd = ImageDraw.Draw(grad)
    for y in range(big):
        gd.line([(0, y), (big, y)], fill=_lerp(BLUE_TOP, BLUE_BOTTOM, y / big) + (255,))
    mask = Image.new("L", (big, big), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, big - 1, big - 1], radius=radius * 4, fill=255)
    img.paste(grad, (0, 0), mask)
    return img.resize((size, size), Image.LANCZOS)


def _cloud(size: int) -> Image.Image:
    """云朵剪影（多个圆 + 圆角矩形底部拼合），返回 alpha 蒙版。"""
    big = size * 4
    m = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(m)
    u = big / 100
    d.rounded_rectangle([20 * u, 58 * u, 80 * u, 76 * u], radius=9 * u, fill=255)
    d.ellipse([22 * u, 42 * u, 46 * u, 66 * u], fill=255)
    d.ellipse([36 * u, 28 * u, 68 * u, 62 * u], fill=255)
    d.ellipse([60 * u, 44 * u, 80 * u, 66 * u], fill=255)
    return m.resize((size, size), Image.LANCZOS)


def _arrows_layer(size: int, color=BLUE_BOTTOM) -> Image.Image:
    """同步双箭头独立图层（绕 (_CX,_CY) 旋转做动画）。"""
    big = size * 4
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    u = big / 100
    cx, cy, r = _CX * u, _CY * u, _R * u
    w = max(2, int(3.2 * u))
    d.arc([cx - r, cy - r, cx + r, cy + r], start=200, end=-20, fill=color, width=w)
    d.arc([cx - r, cy - r, cx + r, cy + r], start=20, end=160, fill=color, width=w)

    def arrowhead(angle_deg, pointing):
        ax = cx + r * math.cos(math.radians(angle_deg))
        ay = cy + r * math.sin(math.radians(angle_deg))
        s = 5.2 * u
        if pointing == "down":
            pts = [(ax - s * 0.62, ay - s * 0.35), (ax + s * 0.62, ay - s * 0.35), (ax, ay + s * 0.75)]
        else:
            pts = [(ax - s * 0.62, ay + s * 0.35), (ax + s * 0.62, ay + s * 0.35), (ax, ay - s * 0.75)]
        d.polygon(pts, fill=color)

    arrowhead(-20, "down")
    arrowhead(200, "up")
    return layer.resize((size, size), Image.LANCZOS)


def _base(size: int) -> Image.Image:
    """静态底座：圆角渐变方块 + 白云（不含箭头）。"""
    img = _rounded_gradient(size, int(size * 0.22))
    cloud = Image.new("RGBA", (size, size), WHITE + (255,))
    cloud.putalpha(_cloud(size))
    img.alpha_composite(cloud)
    return img


def render_icon(size: int = 1024) -> Image.Image:
    img = _base(size)
    img.alpha_composite(_arrows_layer(size))
    return img


def render_frames(size: int = 64, steps: int = 12) -> list[Image.Image]:
    """同步动画帧：箭头绕中心旋转（负角=顺时针）。"""
    base = _base(size)
    arrows = _arrows_layer(size)
    center = (_CX * size / 100, _CY * size / 100)
    frames = []
    for i in range(steps):
        angle = -360.0 * i / steps
        layer = arrows.rotate(angle, center=center, resample=Image.BICUBIC)
        frame = base.copy()
        frame.alpha_composite(layer)
        frames.append(frame)
    return frames


def ensure_icons(assets_dir: Path) -> tuple[Path, Path]:
    """确保 assets/icon.png（256）与 icon.ico 存在，返回 (png, ico) 路径。"""
    assets_dir.mkdir(parents=True, exist_ok=True)
    png = assets_dir / "icon.png"
    ico = assets_dir / "icon.ico"
    if not png.exists():
        render_icon(256).save(png)
    if not ico.exists():
        render_icon(256).save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return png, ico


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "assets"
    png, ico = ensure_icons(out)
    print(f"图标已生成: {png}\n          {ico}")
