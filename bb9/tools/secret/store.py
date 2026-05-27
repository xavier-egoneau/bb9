"""Local named secret store."""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

USER_CONFIG_DIR = Path(os.environ.get("BB9_HOME", Path.home() / ".bb9")).expanduser()
DEFAULT_SECRET_DIR = USER_CONFIG_DIR / "secrets"
NAMED_SECRET_DIR = DEFAULT_SECRET_DIR / "named"
SECRET_REF_PREFIX = "secret:"


def normalize_secret_name(name: str) -> str:
    text = name.strip().upper().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^A-Z0-9_]", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        raise ValueError("empty secret name")
    if text[0].isdigit():
        text = f"SECRET_{text}"
    return text


def secret_ref(name: str) -> str:
    return SECRET_REF_PREFIX + normalize_secret_name(name)


class SecretStore:
    def __init__(self, root: Path = NAMED_SECRET_DIR) -> None:
        self.root = root

    def set(self, name: str, value: str) -> str:
        normalized = normalize_secret_name(name)
        if not value:
            raise ValueError("empty secret value")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(normalized)
        path.write_text(value.strip() + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        return normalized

    def get(self, name: str) -> str:
        path = self._path(normalize_secret_name(name))
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def list_names(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        names = []
        for path in sorted(self.root.glob("*.secret")):
            names.append(path.stem.upper())
        return tuple(names)

    def _path(self, name: str) -> Path:
        return self.root / f"{name.lower()}.secret"


def resolve_secret_ref(ref: str) -> str:
    text = ref.strip()
    if not text.startswith(SECRET_REF_PREFIX):
        return ""
    return SecretStore().get(text.removeprefix(SECRET_REF_PREFIX))
