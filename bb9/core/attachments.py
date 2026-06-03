"""Image attachment helpers for BB9 text channels."""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

IMAGE_REF_RE = re.compile(r"\[image:\s*([^\]]+)\]", re.IGNORECASE)
SUPPORTED_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}
MAX_IMAGE_BYTES = 8_000_000


@dataclass(frozen=True)
class ImageAttachment:
    path: Path
    mime_type: str
    size: int

    def as_data_url(self) -> str:
        payload = base64.b64encode(self.path.read_bytes()).decode("ascii")
        return f"data:{self.mime_type};base64,{payload}"


def strip_image_refs(text: str) -> str:
    return IMAGE_REF_RE.sub("", text).strip()


def image_ref_paths(text: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip().strip("`") for match in IMAGE_REF_RE.finditer(text) if match.group(1).strip())


def resolve_image_attachments(text: str, workspace: Path) -> tuple[ImageAttachment, ...]:
    workspace = Path(workspace).expanduser().resolve(strict=False)
    attachments: list[ImageAttachment] = []
    for raw_path in image_ref_paths(text):
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = workspace / path
        path = path.resolve(strict=False)
        if not _is_allowed_image_path(path, workspace):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size <= 0 or stat.st_size > MAX_IMAGE_BYTES:
            continue
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            continue
        attachments.append(ImageAttachment(path=path, mime_type=mime_type, size=stat.st_size))
    return tuple(attachments)


def image_context_block(attachments: tuple[ImageAttachment, ...]) -> str:
    if not attachments:
        return ""
    lines = ["# Images jointes"]
    for index, image in enumerate(attachments, 1):
        lines.append(f"- image {index}: {image.path} ({image.mime_type}, {image.size} octets)")
    lines.append("")
    lines.append(
        "Si tu peux voir ces images, decris-les directement dans ta reponse. "
        "Si tu ne peux PAS les voir (ton modele ne supporte pas la vision), "
        "tu DOIS appeler BB9_ACTION vision describe path=<chemin_image> pour chaque image "
        "AVANT de repondre a l'utilisateur. Ne dis JAMAIS 'je ne peux pas voir cette image' "
        "ou 'inform the user' a l'utilisateur. Appelle le tool vision, obtiens la description, "
        "puis reponds normalement en integrant cette description."
    )
    return "\n".join(lines)


def _is_allowed_image_path(path: Path, workspace: Path) -> bool:
    allowed_roots = (
        workspace / ".bb9" / "uploads",
        workspace / ".bb9" / "artifacts" / "screenshots",
    )
    for root in allowed_roots:
        root = root.resolve(strict=False)
        if path == root or root in path.parents:
            return True
    return False
