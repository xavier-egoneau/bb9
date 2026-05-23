"""Load standalone tool runtimes from bb9/tools/<name>/runtime.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from .models import Action, GuardianDecision, Observation, RunContext
from .paths import default_tools_dir


def runtime_action_from_text(tool_name: str, text: str) -> Action | None:
    module = _load_runtime(tool_name)
    if module is None or not hasattr(module, "action_from_text"):
        return None
    return module.action_from_text(text)


def review_runtime_action(action: Action, context: RunContext) -> GuardianDecision | None:
    module = _load_runtime(action.name)
    if module is None or not hasattr(module, "review"):
        return None
    return module.review(action, context)


def execute_runtime_tool(action: Action) -> Observation | None:
    module = _load_runtime(action.name)
    if module is None or not hasattr(module, "execute"):
        return None
    return module.execute(action)


def load_tool_module(tool_name: str, module_name: str, root: Path | None = None) -> ModuleType | None:
    return _load_module(tool_name, module_name, root=root, package_prefix="bb9_tool")


def load_skill_module(skill_name: str, module_name: str, root: Path) -> ModuleType | None:
    return _load_module(skill_name, module_name, root=root, package_prefix="bb9_skill")


def _load_runtime(tool_name: str) -> ModuleType | None:
    return _load_module(tool_name, "runtime", package_prefix="bb9_tool")


def _load_module(
    archive_name: str,
    module_name: str,
    *,
    root: Path | None = None,
    package_prefix: str,
) -> ModuleType | None:
    if not _valid_tool_name(archive_name):
        return None
    if not _valid_module_name(module_name):
        return None
    archive_dir = (root or default_tools_dir()) / archive_name
    path = archive_dir / f"{module_name}.py"
    if not path.is_file():
        return None
    package_name = _archive_package_name(package_prefix, archive_name, archive_dir)
    _ensure_archive_package(package_name, archive_dir)
    import_name = f"{package_name}.{module_name}"
    spec = importlib.util.spec_from_file_location(import_name, path)
    if spec is None or spec.loader is None:
        return None
    existing = sys.modules.get(import_name)
    if isinstance(existing, ModuleType):
        return existing
    module = importlib.util.module_from_spec(spec)
    sys.modules[import_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(import_name, None)
        raise
    return module


def _valid_tool_name(name: str) -> bool:
    return bool(name) and all(char.isalnum() or char in {"-", "_"} for char in name)


def _valid_module_name(name: str) -> bool:
    return bool(name) and name.replace("_", "").isalnum()


def _archive_package_name(prefix: str, archive_name: str, archive_dir: Path) -> str:
    safe_name = archive_name.replace("-", "_")
    return f"{prefix}_{safe_name}_{abs(hash(archive_dir.resolve()))}"


def _ensure_archive_package(package_name: str, archive_dir: Path) -> None:
    existing = sys.modules.get(package_name)
    if isinstance(existing, ModuleType):
        return
    package = ModuleType(package_name)
    package.__path__ = [str(archive_dir)]  # type: ignore[attr-defined]
    package.__package__ = package_name
    sys.modules[package_name] = package
