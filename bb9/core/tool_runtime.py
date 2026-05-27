"""Load standalone archive entrypoints for tools and skills."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

from .models import Action, GuardianDecision, Observation, RunContext
from .paths import default_tools_dir

ARCHIVE_KIND_PARAM = "__bb9_archive_kind"
ARCHIVE_NAME_PARAM = "__bb9_archive_name"
ARCHIVE_ROOT_PARAM = "__bb9_archive_root"
ARCHIVE_KIND_TOOL = "tool"
ARCHIVE_KIND_SKILL = "skill"


def runtime_action_from_text(archive_name: str, text: str, context: RunContext | None = None) -> Action | None:
    module, kind, root = _load_action_module_from_context(archive_name, context)
    if module is None or not hasattr(module, "action_from_text"):
        return None
    action = module.action_from_text(text)
    if action is None:
        return None
    return _with_archive_params(action, archive_name=archive_name, kind=kind, root=root)


def review_runtime_action(action: Action, context: RunContext) -> GuardianDecision | None:
    module = _load_action_module_from_action(action)
    if module is None or not hasattr(module, "review"):
        return None
    return module.review(action, context)


def execute_runtime_tool(action: Action) -> Observation | None:
    module = _load_action_module_from_action(action)
    if module is None or not hasattr(module, "execute"):
        return None
    return module.execute(action)


def load_tool_module(tool_name: str, module_name: str, root: Path | None = None) -> ModuleType | None:
    return _load_module(tool_name, module_name, root=root, package_prefix="bb9_tool")


def load_skill_module(skill_name: str, module_name: str, root: Path) -> ModuleType | None:
    return _load_module(skill_name, module_name, root=root, package_prefix="bb9_skill")


def _load_runtime(tool_name: str) -> ModuleType | None:
    return _load_module(tool_name, "runtime", root=default_tools_dir(), package_prefix="bb9_tool")


def _load_action_module_from_context(
    archive_name: str,
    context: RunContext | None,
) -> tuple[ModuleType | None, str, Path]:
    if context is not None:
        for tool in context.tools:
            if tool.name == archive_name and tool.root is not None:
                module = _load_module(archive_name, "runtime", root=tool.root, package_prefix="bb9_tool")
                return module, ARCHIVE_KIND_TOOL, tool.root
        for skill in context.skills:
            if skill.name == archive_name and skill.root is not None:
                module = _load_module(archive_name, "runtime", root=skill.root, package_prefix="bb9_skill")
                return module, ARCHIVE_KIND_SKILL, skill.root
    root = default_tools_dir()
    return _load_module(archive_name, "runtime", root=root, package_prefix="bb9_tool"), ARCHIVE_KIND_TOOL, root


def _load_action_module_from_action(action: Action) -> ModuleType | None:
    kind = str(action.params.get(ARCHIVE_KIND_PARAM) or ARCHIVE_KIND_TOOL)
    root_value = str(action.params.get(ARCHIVE_ROOT_PARAM) or "")
    root = Path(root_value).expanduser() if root_value else default_tools_dir()
    package_prefix = "bb9_skill" if kind == ARCHIVE_KIND_SKILL else "bb9_tool"
    archive_name = str(action.params.get(ARCHIVE_NAME_PARAM) or action.name)
    return _load_module(archive_name, "runtime", root=root, package_prefix=package_prefix)


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
    path = _module_path(archive_dir, module_name)
    if not path.is_file():
        return None
    package_name = _archive_package_name(package_prefix, archive_name, archive_dir)
    _ensure_archive_package(package_name, archive_dir)
    import_name = _import_name(package_name, path, archive_dir, module_name)
    source_mtime = _archive_source_mtime(archive_dir, path)
    existing = sys.modules.get(import_name)
    if isinstance(existing, ModuleType) and getattr(existing, "__bb9_source_mtime_ns__", None) == source_mtime:
        return existing
    if isinstance(existing, ModuleType):
        _drop_archive_submodules(package_name)
        _ensure_archive_package(package_name, archive_dir)
        import_name = _import_name(package_name, path, archive_dir, module_name)
    spec = importlib.util.spec_from_file_location(import_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    module.__bb9_source_mtime_ns__ = source_mtime  # type: ignore[attr-defined]
    sys.modules[import_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(import_name, None)
        raise
    return module


def _module_path(archive_dir: Path, module_name: str) -> Path:
    direct = archive_dir / f"{module_name}.py"
    if direct.is_file():
        return direct
    if module_name == "core":
        nested = archive_dir / "core" / "core.py"
        if nested.is_file():
            return nested
    return direct


def _archive_source_mtime(archive_dir: Path, fallback_path: Path) -> int:
    mtimes = [fallback_path.stat().st_mtime_ns]
    for path in archive_dir.rglob("*.py"):
        try:
            mtimes.append(path.stat().st_mtime_ns)
        except OSError:
            continue
    return max(mtimes)


def _drop_archive_submodules(package_name: str) -> None:
    prefix = f"{package_name}."
    for name in list(sys.modules):
        if name.startswith(prefix):
            sys.modules.pop(name, None)


def _import_name(package_name: str, path: Path, archive_dir: Path, module_name: str) -> str:
    core_dir = archive_dir / "core"
    if module_name == "core" and path.parent == core_dir:
        core_package = f"{package_name}.core"
        _ensure_archive_package(core_package, core_dir)
        return f"{core_package}.core"
    return f"{package_name}.{module_name}"


def _with_archive_params(action: Action, *, archive_name: str, kind: str, root: Path) -> Action:
    params = dict(action.params)
    params.setdefault(ARCHIVE_KIND_PARAM, kind)
    params.setdefault(ARCHIVE_NAME_PARAM, archive_name)
    params.setdefault(ARCHIVE_ROOT_PARAM, str(root))
    return replace(action, params=params)


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
