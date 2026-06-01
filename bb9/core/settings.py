"""User runtime settings."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .models import PermissionProfile
from .paths import bb9_home

SETTINGS_FILE = "settings.json"
PROFILES: tuple[PermissionProfile, ...] = ("safe", "limited", "power")
THEME_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class UserSettings:
    profile: PermissionProfile = "safe"
    web_theme: str = "system"


def default_settings_path() -> Path:
    return bb9_home() / SETTINGS_FILE


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()

    def load(self) -> UserSettings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return UserSettings()
        if not isinstance(raw, dict):
            return UserSettings()
        profile = str(raw.get("profile") or "safe").strip().lower()
        if profile not in PROFILES:
            profile = "safe"
        return UserSettings(profile=cast(PermissionProfile, profile), web_theme=_theme_id(raw.get("web_theme")))

    def save(self, settings: UserSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"profile": settings.profile, "web_theme": settings.web_theme}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def set_profile(self, profile: PermissionProfile) -> None:
        current = self.load()
        self.save(UserSettings(profile=profile, web_theme=current.web_theme))

    def set_web_theme(self, theme: str) -> None:
        current = self.load()
        self.save(UserSettings(profile=current.profile, web_theme=_theme_id(theme)))

    def has_web_theme(self) -> bool:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(raw, dict) and "web_theme" in raw


def settings_from_dict(data: dict[str, Any]) -> UserSettings:
    profile = str(data.get("profile") or "safe").strip().lower()
    if profile not in PROFILES:
        profile = "safe"
    return UserSettings(profile=cast(PermissionProfile, profile), web_theme=_theme_id(data.get("web_theme")))


def _theme_id(value: object) -> str:
    theme = str(value or "system").strip()
    if not theme or not THEME_ID_RE.fullmatch(theme):
        return "system"
    return theme
