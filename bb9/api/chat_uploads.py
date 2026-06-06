"""Image upload helpers for the chat API."""

from __future__ import annotations

import base64
import logging
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from bb9.core.attachments import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_MIME_TYPES

MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

_logger = logging.getLogger("bb9.api")


def save_uploaded_image(*, mime: str, data: str, workspace: Path) -> dict[str, Any]:
    mime = mime.lower().strip()
    if mime not in SUPPORTED_IMAGE_MIME_TYPES or mime not in MIME_EXT:
        return {"ok": False, "error": "unsupported_image_type"}
    try:
        image_bytes = base64.b64decode(data, validate=True)
    except Exception:
        _logger.warning("Failed to decode base64 image data")
        return {"ok": False, "error": "invalid_base64"}
    if not image_bytes:
        return {"ok": False, "error": "empty_image"}
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return {"ok": False, "error": "image_too_large"}

    uploads_dir = workspace / ".bb9" / "uploads" / "web"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    path = uploads_dir / f"{uuid.uuid4().hex[:10]}{MIME_EXT[mime]}"
    path.write_bytes(image_bytes)
    return {
        "ok": True,
        "path": str(path),
        "reference": f"[image: {path}]",
        "url": f"/api/image?path={quote(str(path))}",
        "mime": mime,
        "size": len(image_bytes),
    }
