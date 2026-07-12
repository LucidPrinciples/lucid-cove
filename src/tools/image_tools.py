"""
Image Tools — let agents SEE image files (screenshots, photos) dropped in
Nextcloud, e.g. the shared Inbox where jules land.

The Nextcloud read tool only returns TEXT; handed a PNG it just returns binary
garbage. This tool fetches the image bytes AS THE ACTING PRESENCE (same
request-scoped NC creds the file/calendar tools use) and sends them to a
vision model, returning what it sees as text. So even an agent running on a
text-only model gets a usable description/transcription of the picture.

HEIC/HEIF (iPhone/Pixel) is converted to JPEG first via pillow + pillow-heif.
Those ship in the image (pyproject + requirements.lock) but need an image
REBUILD to land; until then HEIC returns a clear message and the web formats
(PNG/JPG/GIF/WEBP) work with no extra dependency.

Vision model: env VISION_MODEL (a registry id), default 'kimi-k2.5-openrouter'
(Kimi K2.5 is natively multimodal). Resolved through the normal provider
factory, so it honors the Cove's configured keys.
"""

import base64

from langchain_core.tools import tool

from src.env import env
from src.tools.approval import auto
from src.tools.nextcloud_tools import _auth, _find_sibling_by_ws, _webdav_url

# Formats the vision API accepts directly (no conversion needed)
_DIRECT_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",
}
_HEIC_EXTS = {"heic", "heif"}
_MAX_BYTES = 12 * 1024 * 1024  # 12 MB guard (pre-encode)

DEFAULT_VISION_MODEL = "kimi-k2.5-openrouter"


def _heic_to_jpeg(raw: bytes) -> bytes:
    """Convert HEIC/HEIF bytes to JPEG. Raises RuntimeError with a clear
    message if pillow-heif isn't available in this image build."""
    try:
        import io

        import pillow_heif  # manylinux wheel bundles libheif
        from PIL import Image
    except Exception as e:
        raise RuntimeError(
            "HEIC support isn't installed in this container yet. Add 'pillow' "
            "and 'pillow-heif' and rebuild the image "
            "(docker compose up -d --build app). "
            f"(import error: {e})")
    pillow_heif.register_heif_opener()
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


@auto
@tool
async def view_image(path: str, question: str = "") -> str:
    """Look at an image file in Nextcloud and answer a question about it.

    Use this for a screenshot or photo dropped in a folder like your Inbox —
    the regular file-read tool can only read text, this actually SEES the image.
    Supports PNG, JPG, GIF, WEBP, and iPhone HEIC.

    Args:
        path: Nextcloud path to the image, e.g. '/AgentSkills/Inbox/shot.png'
        question: What you want to know. Blank = describe it + transcribe text.
    """
    import httpx

    ext = (path.rsplit(".", 1)[-1] if "." in path else "").lower()
    if ext not in _DIRECT_MIME and ext not in _HEIC_EXTS:
        return (f"'{path}' isn't a supported image (png/jpg/gif/webp/heic). "
                "For text files use nextcloud_read.")

    url = _webdav_url(path)
    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=30) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                alt = await _find_sibling_by_ws(path)
                if alt:
                    resp = await client.get(_webdav_url(alt)); path = alt
    except Exception as e:
        return f"Error fetching {path}: {e}"
    if resp.status_code == 404:
        return (f"Not found: {path}. Check the path — try nextcloud_list on the "
                "folder to see exact filenames.")
    if resp.status_code != 200:
        return f"Error fetching {path}: HTTP {resp.status_code}"

    raw = resp.content
    if len(raw) > _MAX_BYTES:
        return (f"Image is {len(raw) // (1024 * 1024)}MB — over the "
                f"{_MAX_BYTES // (1024 * 1024)}MB view limit.")

    if ext in _HEIC_EXTS:
        try:
            raw = _heic_to_jpeg(raw)
        except RuntimeError as e:
            return str(e)
        except Exception as e:
            return f"Couldn't convert HEIC {path}: {e}"
        mime = "image/jpeg"
    else:
        mime = _DIRECT_MIME[ext]

    data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    prompt = question.strip() or (
        "Describe this image in detail. If it contains any text, transcribe it "
        "exactly. If it's a screenshot of an app, error, or document, explain "
        "clearly what it shows.")

    model_id = env("VISION_MODEL", DEFAULT_VISION_MODEL)
    try:
        from langchain_core.messages import HumanMessage

        from src.models.provider import get_model_client
        client = get_model_client(model_id)
        msg = HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ])
        out = await client.ainvoke([msg])
        text = out.content if isinstance(out.content, str) else str(out.content)
        return f"[image: {path}]\n{text}"
    except Exception as e:
        return (f"Fetched {path} ({len(raw):,} bytes) but the vision model "
                f"'{model_id}' failed: {e}. The acting agent needs to run on a "
                "vision-capable model (e.g. Kimi K2.5), or set VISION_MODEL.")


ALL_IMAGE_TOOLS = [view_image]
TOOLS = ALL_IMAGE_TOOLS  # alias for the cove-core channels.py loader
