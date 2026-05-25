"""Input and output adapters."""

from __future__ import annotations

from .attachments import image_ref_paths
from .models import Intention


def intention_from_text(text: str) -> Intention:
    images = image_ref_paths(text)
    return Intention(text=text, metadata={"image_refs": images} if images else {})
