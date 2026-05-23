"""Input and output adapters."""

from __future__ import annotations

from .models import Intention


def intention_from_text(text: str) -> Intention:
    return Intention(text=text)
