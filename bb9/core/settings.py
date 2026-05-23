"""User runtime settings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .models import PermissionProfile
from .paths import bb9_home


SETTINGS_FILE = "settings.json"
PROFILES: tuple[PermissionProfile, ...] = ("safe", "limited", "power")


@dataclass(frozen=True)
class UserSettings:
    profile: PermissionProfile = "safe"


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
        return UserSettings(profile=cast(PermissionProfile, profile))

    def save(self, settings: UserSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"profile": settings.profile}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def set_profile(self, profile: PermissionProfile) -> None:
        self.save(UserSettings(profile=profile))


def settings_from_dict(data: dict[str, Any]) -> UserSettings:
    profile = str(data.get("profile") or "safe").strip().lower()
    if profile not in PROFILES:
        profile = "safe"
    return UserSettings(profile=cast(PermissionProfile, profile))
