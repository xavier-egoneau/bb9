"""Install BB9 for the current user.

Stdlib-only installer:
- exposes this checkout through the Python user site with a .pth file;
- creates a `bb9` launcher in ~/.local/bin;
- migrates legacy provider config into ~/.bb9.
"""

from __future__ import annotations

import json
import os
import shutil
import site
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
PTH_NAME = "bb9-agentic-system-minimal.pth"
LAUNCHER_NAME = "bb9"
USER_CONFIG_DIR = Path(os.environ.get("BB9_HOME", Path.home() / ".bb9")).expanduser()
OLD_USER_CONFIG_DIR = Path.home() / ".config" / "bb9"
USER_PROVIDER_CONFIG = USER_CONFIG_DIR / "providers.json"
USER_SETTINGS_CONFIG = USER_CONFIG_DIR / "settings.json"
USER_SECRET_DIR = USER_CONFIG_DIR / "secrets"
USER_NAMED_SECRET_DIR = USER_SECRET_DIR / "named"
USER_SKILLS_DIR = USER_CONFIG_DIR / "skills"
USER_AGENTS_DIR = USER_CONFIG_DIR / "agents"
USER_GOALS_DIR = USER_CONFIG_DIR / "goals"
LEGACY_PROVIDER_CONFIG = REPO_ROOT / ".bb9" / "providers.json"
TEMPLATE_AGENTS_DIR = REPO_ROOT / "bb9" / "templates" / "agents"
LEGACY_AGENTS_DIR = REPO_ROOT / "agents"


def main() -> int:
    print("BB9 installer")
    print(f"repo: {REPO_ROOT}")
    pth = install_user_site()
    launcher = install_launcher()
    home = install_user_home()
    migrated = migrate_provider_config()
    print()
    print("Installation terminee.")
    print(f"- python path: {pth}")
    print(f"- commande: {launcher}")
    print(f"- bb9 home: {home}")
    if migrated:
        print(f"- provider config: {USER_PROVIDER_CONFIG}")
    else:
        print("- provider config: aucune migration necessaire")
    if not _path_contains(launcher.parent):
        print()
        print(f"Note: ajoute {launcher.parent} a ton PATH si la commande `bb9` est introuvable.")
    return 0


def install_user_site() -> Path:
    user_site = Path(site.getusersitepackages())
    user_site.mkdir(parents=True, exist_ok=True)
    pth = user_site / PTH_NAME
    pth.write_text(str(REPO_ROOT) + "\n", encoding="utf-8")
    return pth


def install_launcher() -> Path:
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / LAUNCHER_NAME
    launcher.write_text(
        "#!/usr/bin/env sh\n"
        "exec python3 -m bb9 \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher


def install_user_home() -> Path:
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not USER_SETTINGS_CONFIG.exists():
        USER_SETTINGS_CONFIG.write_text('{\n  "profile": "safe"\n}\n', encoding="utf-8")
    USER_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    USER_GOALS_DIR.mkdir(parents=True, exist_ok=True)
    USER_SECRET_DIR.mkdir(parents=True, exist_ok=True)
    USER_NAMED_SECRET_DIR.mkdir(parents=True, exist_ok=True)
    install_default_agents()
    return USER_CONFIG_DIR


def install_default_agents() -> None:
    for source_root in (LEGACY_AGENTS_DIR, TEMPLATE_AGENTS_DIR):
        if not source_root.exists():
            continue
        for source in source_root.iterdir():
            if not source.is_dir():
                continue
            target = USER_AGENTS_DIR / source.name
            copy_missing_tree(source, target)


def migrate_provider_config() -> bool:
    changed = False
    changed = migrate_secret_dir(OLD_USER_CONFIG_DIR) or changed
    changed = migrate_secret_dir(REPO_ROOT / ".bb9") or changed

    source_path = OLD_USER_CONFIG_DIR / "providers.json"
    if not source_path.exists():
        source_path = LEGACY_PROVIDER_CONFIG
    if not source_path.exists():
        return changed

    source = _load_config(source_path)
    source_entries = list(source.get("providers", ()))
    if not source_entries:
        return changed

    migrated_entries = [_migrate_entry(entry) for entry in source_entries if isinstance(entry, dict)]
    if not migrated_entries:
        return changed

    target = _load_config(USER_PROVIDER_CONFIG)
    target_entries = [entry for entry in target.get("providers", ()) if isinstance(entry, dict)]
    existing_ids = {str(entry.get("id", "")) for entry in target_entries}

    for entry in migrated_entries:
        entry_id = str(entry.get("id", ""))
        if entry_id and entry_id in existing_ids:
            continue
        target_entries.append(entry)
        if entry_id:
            existing_ids.add(entry_id)
        changed = True

    active_id = str(target.get("active_id") or "")
    if not active_id:
        source_active = str(source.get("active_id") or "")
        if source_active and any(str(entry.get("id", "")) == source_active for entry in target_entries):
            active_id = source_active
            changed = True
        elif target_entries:
            active_id = str(target_entries[0].get("id", ""))
            changed = True

    if not changed and USER_PROVIDER_CONFIG.exists():
        return False

    USER_PROVIDER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    USER_PROVIDER_CONFIG.write_text(
        json.dumps({"active_id": active_id, "providers": target_entries}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return True


def migrate_secret_dir(source_home: Path) -> bool:
    source_secrets = source_home / "secrets"
    if not source_secrets.exists():
        return False
    changed = False
    USER_SECRET_DIR.mkdir(parents=True, exist_ok=True)
    for source in source_secrets.iterdir():
        target = USER_SECRET_DIR / source.name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)
            changed = True
        elif source.is_dir() and not target.exists():
            shutil.copytree(source, target)
            changed = True
    return changed


def copy_missing_tree(source: Path, target: Path) -> None:
    if not target.exists():
        shutil.copytree(source, target)
        return
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"active_id": "", "providers": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"active_id": "", "providers": []}
    if isinstance(raw, list):
        active_id = str(raw[0].get("id", "")) if raw and isinstance(raw[0], dict) else ""
        return {"active_id": active_id, "providers": raw}
    if isinstance(raw, dict):
        providers = raw.get("providers")
        return {
            "active_id": str(raw.get("active_id") or ""),
            "providers": providers if isinstance(providers, list) else [],
        }
    return {"active_id": "", "providers": []}


def _migrate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(entry)
    metadata = migrated.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        migrated["metadata"] = metadata

    token_path = str(metadata.get("token_path") or "").strip()
    if not token_path:
        return migrated

    source_token = Path(token_path).expanduser()
    if not source_token.is_absolute():
        source_token = REPO_ROOT / source_token
    if not source_token.exists():
        return migrated

    USER_SECRET_DIR.mkdir(parents=True, exist_ok=True)
    target_token = USER_SECRET_DIR / source_token.name
    shutil.copy2(source_token, target_token)
    metadata["token_path"] = str(target_token)
    return migrated


def _path_contains(directory: Path) -> bool:
    target = str(directory)
    return any(part == target for part in os.environ.get("PATH", "").split(os.pathsep))


if __name__ == "__main__":
    raise SystemExit(main())
