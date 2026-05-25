"""Install BB9 for the current user.

Stdlib-only installer:
- exposes this checkout through the Python user site with a .pth file;
- creates a `bb9` launcher in the user command directory;
- adds the user command directory to the user PATH when possible;
- migrates legacy provider config into ~/.bb9.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import site
import sys
from pathlib import Path
from typing import Any


MIN_PYTHON = (3, 11)
PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
PTH_NAME = "bb9-agentic-system-minimal.pth"
LAUNCHER_NAME = "bb9"
POSIX_PATH_MARKER = "# BB9 local commands"
POSIX_PATH_TEMPLATE = """\
# BB9 local commands
case ":$PATH:" in
  *":{bin_dir}:"*) ;;
  *) export PATH="{bin_dir}:$PATH" ;;
esac
"""
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
TEMPLATE_SKILLS_DIR = REPO_ROOT / "bb9" / "templates" / "skills"
LEGACY_AGENTS_DIR = REPO_ROOT / "agents"


def main() -> int:
    if not ensure_supported_python():
        return 1
    print("BB9 installer")
    print(f"repo: {REPO_ROOT}")
    pth = install_user_site()
    launcher = install_launcher()
    path_changed = ensure_user_path(launcher.parent)
    home = install_user_home()
    migrated = migrate_provider_config()
    print()
    print("Installation terminee.")
    print(f"- python path: {pth}")
    print(f"- commande: {launcher}")
    print(f"- PATH utilisateur: {'mis a jour' if path_changed else 'deja configure'}")
    print(f"- bb9 home: {home}")
    if migrated:
        print(f"- provider config: {USER_PROVIDER_CONFIG}")
    else:
        print("- provider config: aucune migration necessaire")
    if not _path_contains(launcher.parent):
        print()
        print("Note: ouvre un nouveau terminal pour recuperer le PATH mis a jour.")
    return 0


def ensure_supported_python() -> bool:
    if sys.version_info >= MIN_PYTHON:
        return True
    required = ".".join(str(part) for part in MIN_PYTHON)
    current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(f"Erreur: BB9 demande Python {required}+ ; Python courant: {current}")
    print("Relance avec Python 3.11+, par exemple: python3.11 -m bb9.install")
    print("Sous Windows: py -3.11 -m bb9.install")
    return False


def install_user_site(user_site: Path | None = None) -> Path:
    user_site = user_site or Path(site.getusersitepackages())
    user_site.mkdir(parents=True, exist_ok=True)
    pth = user_site / PTH_NAME
    pth.write_text(str(REPO_ROOT) + "\n", encoding="utf-8")
    return pth


def default_user_bin_dir() -> Path:
    if os.name == "nt":
        return Path(site.getuserbase()) / "Scripts"
    return Path.home() / ".local" / "bin"


def install_launcher(
    bin_dir: Path | None = None,
    python_executable: str = "",
    *,
    os_name: str | None = None,
) -> Path:
    os_name = os_name or os.name
    bin_dir = bin_dir or default_user_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = python_executable or sys.executable

    if os_name == "nt":
        cmd_launcher = bin_dir / f"{LAUNCHER_NAME}.cmd"
        cmd_launcher.write_text(
            "@echo off\r\n"
            f"\"{executable}\" -m bb9 %*\r\n",
            encoding="utf-8",
        )
        ps_launcher = bin_dir / f"{LAUNCHER_NAME}.ps1"
        ps_launcher.write_text(
            f"& {powershell_quote(executable)} -m bb9 @args\r\n",
            encoding="utf-8",
        )
        return cmd_launcher

    launcher = bin_dir / LAUNCHER_NAME
    launcher.write_text(
        "#!/usr/bin/env sh\n"
        f"exec {shlex.quote(executable)} -m bb9 \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher


def ensure_user_path(bin_dir: Path, *, os_name: str | None = None, home: Path | None = None) -> bool:
    os_name = os_name or os.name
    if _path_contains(bin_dir):
        return False
    if os_name == "nt":
        return ensure_windows_user_path(bin_dir)
    return ensure_posix_user_path(bin_dir, home=home)


def ensure_posix_user_path(bin_dir: Path, *, home: Path | None = None) -> bool:
    home = home or Path.home()
    changed = False
    for profile in posix_profile_paths(home):
        changed = append_posix_path(profile, bin_dir) or changed
    return changed


def posix_profile_paths(home: Path) -> tuple[Path, ...]:
    return (home / ".zshrc", home / ".bashrc", home / ".profile")


def append_posix_path(profile: Path, bin_dir: Path) -> bool:
    text = profile.read_text(encoding="utf-8") if profile.exists() else ""
    bin_text = str(bin_dir)
    cleaned = remove_posix_path_blocks(text)
    block = POSIX_PATH_TEMPLATE.format(bin_dir=bin_text)
    prefix = cleaned.rstrip() + "\n\n" if cleaned.strip() else ""
    updated = prefix + block
    if updated.rstrip() == text.rstrip():
        return False
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(updated, encoding="utf-8")
    return True


def remove_posix_path_blocks(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != POSIX_PATH_MARKER:
            kept.append(lines[index])
            index += 1
            continue

        index += 1
        if index < len(lines) and lines[index].strip().startswith("case "):
            while index < len(lines) and lines[index].strip() != "esac":
                index += 1
            if index < len(lines):
                index += 1
            continue
        if index < len(lines) and "PATH=" in lines[index]:
            index += 1
            continue
    return "\n".join(kept).rstrip()


def ensure_windows_user_path(bin_dir: Path) -> bool:
    try:
        import winreg
    except ImportError:
        return False

    key_path = "Environment"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, value_type = "", winreg.REG_EXPAND_SZ
        updated = append_path_value(str(current), bin_dir, os_name="nt")
        if updated == current:
            return False
        winreg.SetValueEx(key, "Path", 0, value_type, updated)
    broadcast_windows_environment_change()
    return True


def broadcast_windows_environment_change() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
    except ImportError:
        return
    hwnd_broadcast = 0xFFFF
    wm_settingchange = 0x001A
    smto_abortifhung = 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        hwnd_broadcast,
        wm_settingchange,
        0,
        "Environment",
        smto_abortifhung,
        5000,
        None,
    )


def append_path_value(path_value: str, bin_dir: Path, *, os_name: str | None = None) -> str:
    os_name = os_name or os.name
    separator = ";" if os_name == "nt" else ":"
    target = str(bin_dir)
    parts = [part for part in path_value.split(separator) if part]
    if any(_same_path_text(part, target, os_name=os_name) for part in parts):
        return path_value
    if not path_value:
        return target
    return path_value.rstrip(separator) + separator + target


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
    install_default_skills()
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


def install_default_skills() -> None:
    if not TEMPLATE_SKILLS_DIR.exists():
        return
    for source in TEMPLATE_SKILLS_DIR.iterdir():
        if not source.is_dir():
            continue
        target = USER_SKILLS_DIR / source.name
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
    return any(
        _same_path_text(part, target, os_name=os.name)
        for part in os.environ.get("PATH", "").split(os.pathsep)
    )


def _same_path_text(left: str, right: str, *, os_name: str) -> bool:
    if os_name == "nt":
        return left.rstrip("\\/").lower() == right.rstrip("\\/").lower()
    return left.rstrip("/") == right.rstrip("/")


if __name__ == "__main__":
    raise SystemExit(main())
