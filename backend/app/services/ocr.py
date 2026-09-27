"""本地 OCR 引擎（RapidOCR 优先，Tesseract 兜底）。

图片/扫描 PDF 的处理策略是三级递进：文字提取 → 本地 OCR → 视觉模型。
本模块只负责"OCR"这一级，依赖可选：未安装或初始化失败时返回空串，
由 ingestion 回退到视觉模型，不阻塞主流程。

OCR_PROVIDER 配置：
  auto      本地 OCR 优先（RapidOCR → Tesseract），均不可用时回退视觉
  rapidocr  强制 RapidOCR（不可用回退视觉）
  tesseract 强制 Tesseract（不可用回退视觉）
  vision    跳过本地 OCR，直接用视觉模型（最慢但零额外依赖）
"""
import io
import logging
import shutil

import numpy as np
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

_rapid_engine = None
_rapid_init_done = False
_tess_init_done = False
_tess_ok = False


def _rapid_available() -> bool:
    """初始化（惰性）RapidOCR 引擎；失败返回 False，调用方回退视觉。"""
    global _rapid_engine, _rapid_init_done
    if _rapid_init_done:
        return _rapid_engine is not None
    _rapid_init_done = True
    try:
        from rapidocr_onnxruntime import RapidOCR

        model_root = settings.ocr_models_path
        model_root.mkdir(parents=True, exist_ok=True)
        _rapid_engine = RapidOCR(model_root=str(model_root))
        logger.info("RapidOCR 本地 OCR 引擎就绪（模型目录 %s）", model_root)
    except Exception as exc:
        _rapid_engine = None
        logger.warning("RapidOCR 不可用（将回退 Tesseract/视觉）: %s", exc)
    return _rapid_engine is not None


def _tesseract_available() -> bool:
    """检测系统 tesseract 与 pytesseract 是否可用。"""
    global _tess_init_done, _tess_ok
    if _tess_init_done:
        return _tess_ok
    _tess_init_done = True
    try:
        import pytesseract  # noqa: F401

        _tess_ok = shutil.which("tesseract") is not None
        if not _tess_ok:
            logger.warning("tesseract 未安装（apt install tesseract-ocr tesseract-ocr-chi-sim）")
    except Exception as exc:
        _tess_ok = False
        logger.warning("pytesseract 不可用: %s", exc)
    return _tess_ok


def ocr_provider_name() -> str:
    """当前实际可用的 OCR 引擎名；空串表示无本地 OCR（调用方回退视觉）。"""
    cfg = (settings.OCR_PROVIDER or "auto").strip().lower()
    if cfg == "vision":
        return ""
    if cfg == "rapidocr":
        return "rapidocr" if _rapid_available() else ""
    if cfg == "tesseract":
        return "tesseract" if _tesseract_available() else ""
    if _rapid_available():
        return "rapidocr"
    if _tesseract_available():
        return "tesseract"
    return ""


def _ocr_pil(img: Image.Image) -> str:
    provider = ocr_provider_name()
    if not provider:
        return ""
    if provider == "rapidocr":
        result, _ = _rapid_engine(np.asarray(img))
        if not result:
            return ""
        return "\n".join(str(item[1]) for item in result if len(item) > 1).strip()
    import pytesseract

    return pytesseract.image_to_string(img, lang="chi_sim+eng").strip()


def ocr_image_bytes(img_bytes: bytes) -> str:
    """对图片字节做本地 OCR；失败/无文字返回空串（调用方据此回退视觉）。"""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as exc:
        logger.warning("OCR 读取图片失败: %s", exc)
        return ""
    try:
        return _ocr_pil(img)
    except Exception as exc:
        logger.warning("本地 OCR 调用失败（回退视觉）: %s", exc)
        return ""
