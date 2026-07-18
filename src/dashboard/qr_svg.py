"""QR SVG helper for mesh phone join (#MESH2).

Wraps the pure-Python ``qrcode`` package so Mission Control can inline an SVG
of a join deep-link without shipping a JS QR library. Pillow is already a
Cove dependency; ``qrcode`` is added for encoding.
"""
from __future__ import annotations

from io import BytesIO


def qr_svg(text: str, *, box_size: int = 6, border: int = 2) -> str:
    """Encode *text* as a standalone SVG string (black modules on white)."""
    import qrcode
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
        image_factory=SvgPathImage,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image()
    buf = BytesIO()
    img.save(buf)
    raw = buf.getvalue().decode("utf-8")
    # SvgPathImage may emit an XML declaration — strip it for safe HTML inline.
    if raw.startswith("<?xml"):
        raw = raw.split("?>", 1)[-1].lstrip()
    # Force responsive sizing so MC can cap the box without tiny mm-units.
    if 'width="' in raw and "style=" not in raw[:80]:
        raw = raw.replace("<svg ", '<svg style="width:100%;height:auto;display:block;" ', 1)
    return raw
