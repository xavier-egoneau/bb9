"""REPL entrypoint for the dev skill."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from bb9.core import delegation as delegation_core
from bb9.core.agents import AgentNotFoundError, load_subagent
from bb9.core.delegation import delegate
from bb9.core.kernel import Kernel
from bb9.core.loop import run_once
from bb9.core.models import AgentProfile, Intention, PermissionProfile, RunContext, Task, TaskResult, TraceEvent

PROFILES = {"safe", "limited", "power"}
RESULT_FIELDS = {"status", "summary", "blockers", "evidence"}
Emit = Callable[[str], None]


@dataclass(frozen=True)
class BuildTaskReport:
    task: Task
    result: TaskResult
    title: str
    worker: str = ""
    trace: tuple[TraceEvent, ...] = ()


@dataclass(frozen=True)
class BuildResult:
    plan_path: Path | None
    total: int = 0
    completed_before: frozenset[str] = frozenset()
    reports: tuple[BuildTaskReport, ...] = ()
    error: str = ""
    approval_pending: bool = False

    @property
    def ok(self) -> bool:
        return not self.error and not self.approval_pending and all(report.result.status == "done" for report in self.reports)

    @property
    def has_errors(self) -> bool:
        return bool(self.error) or self.approval_pending or any(report.result.status != "done" for report in self.reports)

    @property
    def executed_results(self) -> list[TaskResult]:
        completed = set(self.completed_before)
        return [report.result for report in self.reports if report.result.task_id not in completed]


def register(cli) -> None:
    cli.add_command("/build", lambda rest: _run(cli, rest), "exécuter un plan ou déléguer une tâche")


def _run(cli, rest: str) -> bool:
    command, _, value = rest.strip().partition(" ")
    if command == "run":
        return _run_plan(cli, value)
    if command != "delegate":
        if not rest.strip():
            return _run_plan(cli, "")
        cli.run_intention(("/build " + rest).strip())
        return True

    params = _parse_params(value)
    task = _task_from_params(params)
    try:
        subagent = _load_worker(cli, params.get("worker", "default"))
        parent_context = cli.build_context()
    except AgentNotFoundError as exc:
        print(f"task... {task.title}: error")
        print(f"blocker... {exc}")
        return True

    result = delegate(
        task,
        subagent,
        parent_context,
        lambda intention, context: _run_subagent(cli, intention, context),
    )
    _print_result(result, task.title, {})
    return True


def _run_plan(cli, rest: str) -> bool:
    build_plan(cli, rest, emit=print)
    return True


def build_plan(cli, rest: str = "", *, emit: Emit = print) -> BuildResult:
    try:
        plan_path = _plan_path(rest)
        plan_text = plan_path.read_text(encoding="utf-8")
        completed = completed_task_ids(plan_text)
        errored = errored_task_ids(plan_text)
        tasks = parse_plan(plan_text)
    except (OSError, ValueError) as exc:
        emit("plan... error")
        emit(f"blocker... {exc}")
        return BuildResult(plan_path=None, error=str(exc))

    retry_errors = _retry_errors(rest)
    if not tasks:
        if completed:
            emit(f"Rien de nouveau à exécuter. Le plan est déjà à jour dans {_workspace_relative(plan_path)}.")
            return BuildResult(plan_path=plan_path, completed_before=frozenset(completed))
        emit("plan... error")
        emit("blocker... no task found")
        return BuildResult(plan_path=plan_path, error="no task found")

    emit(f"plan... {len(tasks)} task(s)")
    title_by_id = {task.id: task.title for task in tasks}
    results: dict[str, TaskResult] = {}
    reports: list[BuildTaskReport] = []
    for task_id in completed:
        results[task_id] = TaskResult(task_id=task_id, status="done", summary="Already checked in plan.")
    if not retry_errors:
        for task_id in errored - completed:
            results[task_id] = TaskResult(
                task_id=task_id,
                status="error",
                summary="Task already marked as error in plan; explicit retry required.",
                blockers=("previous_error",),
            )
    pending = [task for task in tasks if task.id not in completed and (retry_errors or task.id not in errored)]
    if not pending and errored and not retry_errors:
        error = f"{len(errored - completed)} task(s) already in error; use /build --retry-errors to retry"
        emit("plan... blocked")
        emit(f"blocker... {error}")
        return BuildResult(
            plan_path=plan_path,
            total=len(tasks),
            completed_before=frozenset(completed),
            error=error,
        )
    while pending:
        ready, blocked, waiting = _partition_tasks(pending, results)
        for task, failed_dependencies in blocked:
            result = TaskResult(
                task_id=task.id,
                status="error",
                summary="Task skipped because dependencies are not done.",
                blockers=tuple(f"dependency:{dep}" for dep in failed_dependencies),
            )
            results[task.id] = result
            write_task_state(plan_path, task.id, result)
            report = BuildTaskReport(task=task, result=result, title=task.title)
            reports.append(report)
            _print_result(result, task.title, title_by_id, emit=emit)
        if not ready:
            for task in waiting:
                result = TaskResult(
                    task_id=task.id,
                    status="error",
                    summary="Task skipped because dependencies could not be resolved.",
                    blockers=tuple(f"dependency:{dep}" for dep in task.dependencies if dep not in results),
                )
                results[task.id] = result
                write_task_state(plan_path, task.id, result)
                report = BuildTaskReport(task=task, result=result, title=task.title)
                reports.append(report)
                _print_result(result, task.title, title_by_id, emit=emit)
            break

        parallel_group = [] if _serial_build_for_approvals(cli) else _parallel_group(ready)
        if len(parallel_group) > 1:
            emit("parallel... " + _human_list(task.title for task in parallel_group))
            for report in _execute_parallel(cli, parallel_group, plan_path=plan_path, emit=emit):
                result = report.result
                if _report_needs_approval(report):
                    reports.append(report)
                    _print_result(result, title_by_id.get(result.task_id, result.task_id), title_by_id, emit=emit)
                    emit(f"ask... validation guardian requise pour {report.title}")
                    return BuildResult(
                        plan_path=plan_path,
                        total=len(tasks),
                        completed_before=frozenset(completed),
                        reports=tuple(reports),
                        approval_pending=True,
                    )
                results[result.task_id] = result
                if result.status == "done":
                    mark_task_done(plan_path, result.task_id)
                write_task_state(plan_path, result.task_id, result)
                reports.append(report)
                _print_result(result, title_by_id.get(result.task_id, result.task_id), title_by_id, emit=emit)
            ran = {task.id for task in parallel_group}
        else:
            task = ready[0]
            report = _execute_task(cli, task, plan_path=plan_path, emit=emit)
            result = report.result
            if _report_needs_approval(report):
                reports.append(report)
                _print_result(result, task.title, title_by_id, emit=emit)
                emit(f"ask... validation guardian requise pour {task.title}")
                return BuildResult(
                    plan_path=plan_path,
                    total=len(tasks),
                    completed_before=frozenset(completed),
                    reports=tuple(reports),
                    approval_pending=True,
                )
            results[task.id] = result
            if result.status == "done":
                mark_task_done(plan_path, task.id)
            write_task_state(plan_path, task.id, result)
            reports.append(report)
            _print_result(result, task.title, title_by_id, emit=emit)
            ran = {task.id}
        pending = [task for task in pending if task.id not in ran and task.id not in results]

    executed = [result for task_id, result in results.items() if task_id not in completed]
    emit(_recap(executed, title_by_id, plan_path))
    return BuildResult(
        plan_path=plan_path,
        total=len(tasks),
        completed_before=frozenset(completed),
        reports=tuple(reports),
    )


def _partition_tasks(
    tasks: list[Task],
    results: dict[str, TaskResult],
) -> tuple[list[Task], list[tuple[Task, list[str]]], list[Task]]:
    ready: list[Task] = []
    blocked: list[tuple[Task, list[str]]] = []
    waiting: list[Task] = []
    for task in tasks:
        missing = [dep for dep in task.dependencies if dep not in results]
        failed = [dep for dep in task.dependencies if dep in results and results[dep].status != "done"]
        if failed:
            blocked.append((task, failed))
        elif missing:
            waiting.append(task)
        else:
            ready.append(task)
    return ready, blocked, waiting


def _parallel_group(tasks: list[Task]) -> list[Task]:
    group: list[Task] = []
    touched: list[Path] = []
    for task in tasks:
        if not task.parallelizable or not task.paths:
            continue
        paths = [_normalized_task_path(path) for path in task.paths]
        if any(_paths_overlap(path, existing) for path in paths for existing in touched):
            continue
        group.append(task)
        touched.extend(paths)
    return group


def _normalized_task_path(path: str) -> Path:
    value = path.strip()
    candidate = Path(value or ".").expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve(strict=False)


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _serial_build_for_approvals(cli) -> bool:
    return bool(getattr(cli, "serial_build_for_approvals", False))


def _execute_parallel(cli, tasks: list[Task], *, plan_path: Path | None = None, emit: Emit = print) -> list[BuildTaskReport]:
    reports_by_id: dict[str, BuildTaskReport] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {executor.submit(_execute_task, cli, task, plan_path=plan_path, emit=emit): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                reports_by_id[task.id] = future.result()
            except Exception as exc:
                result = TaskResult(
                    task_id=task.id,
                    status="error",
                    summary=f"Parallel task failed: {exc}",
                    blockers=(exc.__class__.__name__,),
                )
                reports_by_id[task.id] = BuildTaskReport(task=task, result=result, title=task.title)
    return [reports_by_id[task.id] for task in tasks]


def _execute_task(cli, task: Task, *, plan_path: Path | None = None, emit: Emit = print) -> BuildTaskReport:
    try:
        subagent = _load_worker(cli, task.suggested_worker or "default")
        parent_context = cli.build_context()
    except AgentNotFoundError as exc:
        result = TaskResult(
            task_id=task.id,
            status="error",
            summary="Worker not available.",
            blockers=(str(exc),),
        )
        return BuildTaskReport(task=task, result=result, title=task.title)

    emit(f"task... {task.title}: start {subagent.name}")
    _begin_build_task(cli, task, plan_path, subagent.name)
    try:
        execution = _delegate_task(
            task,
            subagent,
            parent_context,
            lambda intention, context: _run_subagent(cli, intention, context),
        )
    finally:
        _end_build_task(cli, task)
    return BuildTaskReport(
        task=task,
        result=execution.result,
        title=task.title,
        worker=subagent.name,
        trace=execution.trace,
    )


def _begin_build_task(cli, task: Task, plan_path: Path | None, worker_name: str = "") -> None:
    hook = getattr(cli, "begin_build_task", None)
    if callable(hook):
        try:
            hook(task, plan_path, worker_name)
        except TypeError:
            hook(task, plan_path)


def _end_build_task(cli, task: Task) -> None:
    hook = getattr(cli, "end_build_task", None)
    if callable(hook):
        hook(task)


@dataclass(frozen=True)
class _TaskExecution:
    result: TaskResult
    trace: tuple[TraceEvent, ...] = ()


def _delegate_task(task, subagent, parent_context, runner) -> _TaskExecution:
    delegate_func = globals().get("delegate")
    if delegate_func is not None and delegate_func is not delegation_core.delegate:
        return _TaskExecution(result=delegate_func(task, subagent, parent_context, runner))
    detailed = delegation_core.delegate_detailed(task, subagent, parent_context, runner)
    return _TaskExecution(result=detailed.task_result, trace=detailed.trace)


def _run_subagent(cli, intention: Intention, context: RunContext):
    return run_once(
        Kernel(provider=cli.build_provider_for_agent(context.agent)),
        intention,
        context,
        ask_user=cli.ask_guardian,
    )


def _load_worker(cli, worker: str) -> AgentProfile:
    name = worker.strip() or "default"
    if "/" in name:
        parent, _, subagent = name.partition("/")
        return load_subagent(cli.state.agents_dir, parent, subagent)
    return load_subagent(cli.state.agents_dir, cli.state.agent_name, name)


def _task_from_params(params: dict[str, str]) -> Task:
    profile = _profile(params.get("profile", params.get("permission_profile", "")))
    return Task(
        id=params.get("id", "manual"),
        title=params.get("title", params.get("goal", "Delegated task")),
        goal=params.get("goal", ""),
        context=params.get("context", ""),
        inputs=_items(params.get("inputs", "")),
        paths=_items(params.get("paths", "")),
        expected_output=params.get("expected", params.get("expected_output", "")),
        done_criteria=_items(params.get("done", params.get("done_criteria", ""))),
        dependencies=_items(params.get("dependencies", "")),
        parallelizable=params.get("parallelizable", "").lower() in {"1", "true", "yes", "oui"},
        suggested_worker=params.get("worker", "default"),
        permission_profile=profile,
        tool_scope=params.get("tool_scope", params.get("scope", "dev")) or "dev",
        max_iterations=_int_value(params.get("max_iterations", ""), default=1),
    )


def parse_plan(text: str) -> tuple[Task, ...]:
    checkbox_tasks = parse_checkbox_plan(text)
    if checkbox_tasks or any(_checkbox_task(line) is not None for line in text.splitlines()):
        return checkbox_tasks
    blocks: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith(("## Task", "### Task")):
            if current_title or current_lines:
                blocks.append((current_title, current_lines))
            current_title = stripped.lstrip("#").strip().removeprefix("Task").strip(" :-")
            current_lines = []
            continue
        if current_title or current_lines:
            current_lines.append(raw_line)
    if current_title or current_lines:
        blocks.append((current_title, current_lines))
    return tuple(_task_from_block(index, title, lines) for index, (title, lines) in enumerate(blocks, 1))


def parse_checkbox_plan(text: str) -> tuple[Task, ...]:
    blocks = _checkbox_blocks(text)
    return tuple(
        _task_from_checkbox_block(task_id, title, lines)
        for task_id, title, done, lines in blocks
        if not done
    )


def _checkbox_blocks(text: str) -> tuple[tuple[str, str, bool, list[str]], ...]:
    blocks: list[tuple[str, str, bool, list[str]]] = []
    current_id = ""
    current_title = ""
    current_done = False
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        parsed = _checkbox_task(raw_line)
        if parsed is not None:
            if current_id:
                blocks.append((current_id, current_title, current_done, current_lines))
            current_id, current_title, current_done = parsed
            current_lines = []
            continue
        if current_id:
            current_lines.append(raw_line)
    if current_id:
        blocks.append((current_id, current_title, current_done, current_lines))
    return tuple(blocks)


def completed_task_ids(text: str) -> set[str]:
    completed: set[str] = set()
    for task_id, _title, done, lines in _checkbox_blocks(text):
        if done or _stored_task_status(lines) == "done":
            completed.add(task_id)
    return completed


def errored_task_ids(text: str) -> set[str]:
    errored: set[str] = set()
    for task_id, _title, _done, lines in _checkbox_blocks(text):
        if _stored_task_status(lines) == "error" and not _dependency_only_state(lines):
            errored.add(task_id)
    return errored


def _stored_task_status(lines: list[str]) -> str:
    fields = _fields(lines)
    summary_status = delegation_core.explicit_status_from_summary(fields.get("summary", ""))
    status = fields.get("status", "").strip().lower()
    if status == "done" or summary_status == "done":
        return "done"
    if status == "error" or summary_status == "error":
        return "error"
    return ""


def _dependency_only_state(lines: list[str]) -> bool:
    fields = _fields(lines)
    if _dependency_skip_summary(fields.get("summary", "")):
        return True
    blockers = tuple(item.strip() for item in re.split(r"[;,]\s*", fields.get("blockers", "")) if item.strip())
    return bool(blockers) and all(blocker.startswith("dependency:") for blocker in blockers)


def _dependency_skip_summary(value: str) -> bool:
    summary = " ".join(str(value or "").lower().split())
    return "dependencies are not done" in summary or "dependencies could not be resolved" in summary


def mark_task_done(path: Path, task_id: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    for line in lines:
        parsed = _checkbox_task(line)
        if parsed is not None and parsed[0] == task_id and not parsed[2]:
            updated.append(line.replace("[ ]", "[x]", 1))
            continue
        updated.append(line)
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def write_task_state(path: Path, task_id: str, result: TaskResult) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    inside_target = False
    found = False
    inserted = False

    for line in lines:
        parsed = _checkbox_task(line)
        if parsed is not None:
            if inside_target and not inserted:
                _append_state_lines(updated, result)
                inserted = True
            inside_target = parsed[0] == task_id
            found = found or inside_target
            if inside_target:
                inserted = False
            updated.append(line)
            continue

        if inside_target and _is_state_line(line):
            continue
        updated.append(line)

    if inside_target and not inserted:
        _append_state_lines(updated, result)
    if found:
        path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def _append_state_lines(lines: list[str], result: TaskResult) -> None:
    trailing_blank: list[str] = []
    while lines and not lines[-1].strip():
        trailing_blank.append(lines.pop())
    lines.extend(_state_lines(result))
    lines.extend(reversed(trailing_blank))


def _state_lines(result: TaskResult) -> list[str]:
    lines = [
        f"  status: {result.status}",
        f"  summary: {_one_line(result.summary)}",
    ]
    if result.blockers:
        lines.append("  blockers: " + _one_line("; ".join(result.blockers)))
    if result.evidence:
        lines.append("  evidence: " + _one_line(" | ".join(result.evidence[:3])))
    return lines


def _is_state_line(line: str) -> bool:
    stripped = line.strip()
    if ":" not in stripped:
        return False
    key, _, _ = stripped.partition(":")
    return key.strip().lower().replace("-", "_") in RESULT_FIELDS


def _task_from_checkbox_block(task_id: str, title: str, lines: list[str]) -> Task:
    fields = _fields(lines)
    worker = fields.get("worker", fields.get("suggested_worker", "default"))
    return Task(
        id=task_id,
        title=fields.get("title", title or task_id),
        goal=fields.get("goal", ""),
        context=fields.get("context", ""),
        inputs=_items(fields.get("inputs", "")),
        paths=_items(fields.get("paths", "")),
        expected_output=fields.get("expected", fields.get("expected_output", "")),
        done_criteria=_items(fields.get("done", fields.get("done_criteria", ""))),
        dependencies=_items(fields.get("depends", fields.get("dependencies", ""))),
        parallelizable=fields.get("parallelizable", "").lower() in {"1", "true", "yes", "oui"},
        suggested_worker=worker,
        permission_profile=_profile(fields.get("profile", fields.get("permission_profile", ""))),
        tool_scope=fields.get("tool_scope", fields.get("scope", "dev")) or "dev",
        max_iterations=_int_value(fields.get("max_iterations", ""), default=1),
    )


def _checkbox_task(line: str) -> tuple[str, str, bool] | None:
    stripped = line.strip()
    if not stripped.startswith("- ["):
        return None
    marker = stripped[3:4].lower()
    if marker not in {" ", "x"} or not stripped.startswith("] ", 4):
        return None
    rest = stripped[6:].strip()
    if not rest:
        return None
    task_id, _, title = rest.partition(" ")
    return task_id.strip(), title.strip(), marker == "x"


def _task_from_block(index: int, title: str, lines: list[str]) -> Task:
    fields = _fields(lines)
    task_id = fields.get("id") or _first_title_token(title) or f"T{index}"
    worker = fields.get("worker", fields.get("suggested_worker", "default"))
    return Task(
        id=task_id,
        title=fields.get("title", title or task_id),
        goal=fields.get("goal", ""),
        context=fields.get("context", ""),
        inputs=_items(fields.get("inputs", "")),
        paths=_items(fields.get("paths", "")),
        expected_output=fields.get("expected", fields.get("expected_output", "")),
        done_criteria=_items(fields.get("done", fields.get("done_criteria", ""))),
        dependencies=_items(fields.get("depends", fields.get("dependencies", ""))),
        parallelizable=fields.get("parallelizable", "").lower() in {"1", "true", "yes", "oui"},
        suggested_worker=worker,
        permission_profile=_profile(fields.get("profile", fields.get("permission_profile", ""))),
        tool_scope=fields.get("tool_scope", fields.get("scope", "dev")) or "dev",
        max_iterations=_int_value(fields.get("max_iterations", ""), default=1),
    )


def _fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-", "*")) or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        normalized = key.strip().lower().replace("-", "_")
        if normalized:
            fields[normalized] = value.strip()
    return fields


def _first_title_token(title: str) -> str:
    return title.split(maxsplit=1)[0].strip() if title.strip() else ""


def _plan_path(rest: str) -> Path:
    params = _parse_params(rest)
    raw = params.get("file", params.get("path", ""))
    if not raw:
        for token in shlex.split(rest):
            if "=" not in token and not token.startswith("-"):
                raw = token
                break
    if not raw:
        raw = ".bb9/plan.md"
    workspace = Path.cwd().resolve()
    path = (workspace / raw).expanduser().resolve()
    if path != workspace and workspace not in path.parents:
        raise ValueError("plan file must stay inside the workspace")
    if not path.is_file():
        raise ValueError(f"plan file not found: {raw}")
    return path


def _parse_params(text: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for token in shlex.split(text):
        key, separator, value = token.partition("=")
        if not separator:
            continue
        normalized = key.strip().lower().replace("-", "_")
        if normalized:
            params[normalized] = value.strip()
    return params


def _retry_errors(text: str) -> bool:
    params = _parse_params(text)
    value = params.get("retry_errors", params.get("retry", ""))
    if value.lower() in {"1", "true", "yes", "oui"}:
        return True
    tokens = {token.strip().lower() for token in shlex.split(text) if token.strip()}
    return bool(tokens & {"--retry-errors", "retry-errors", "--retry", "retry"})


def _profile(value: str) -> PermissionProfile | None:
    text = value.strip().lower()
    if text in PROFILES:
        return text  # type: ignore[return-value]
    return None


def _items(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in re.split(r"[\s,]+", value) if item.strip())


def _int_value(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(1, parsed)


def _print_result(result: TaskResult, title: str, title_by_id: dict[str, str], *, emit: Emit = print) -> None:
    label = title or title_by_id.get(result.task_id, result.task_id)
    emit(f"task... {label}: {result.status}")
    emit(f"sum... {result.summary}")
    if result.changed:
        emit("chg... " + ", ".join(result.changed))
    if result.observed:
        emit("obs... " + ", ".join(result.observed))
    if result.blockers:
        emit("blk... " + "; ".join(_human_blockers(result.blockers, title_by_id)))
    if result.evidence:
        emit("evd... " + " | ".join(result.evidence[:3]))
    if result.next_suggestion:
        emit(f"nxt... {result.next_suggestion}")


def _recap(results: list[TaskResult], title_by_id: dict[str, str], plan_path: Path) -> str:
    done = [title_by_id.get(result.task_id, result.task_id) for result in results if result.status == "done"]
    errors = [result for result in results if result.status != "done"]
    plan_label = _workspace_relative(plan_path)

    if done and not errors:
        return f"J'ai terminé {_human_list(done)}. Le plan est à jour dans {plan_label}."

    parts: list[str] = []
    if done:
        parts.append(f"J'ai terminé {_human_list(done)}.")
    if errors:
        titles = [title_by_id.get(result.task_id, result.task_id) for result in errors]
        blockers = _recap_blockers(errors, title_by_id)
        sentence = f"Je n'ai pas pu terminer {_human_list(titles)}"
        if blockers:
            sentence += f", parce que {blockers}"
        sentence += "."
        parts.append(sentence)
        parts.append("Le prochain pas utile est de corriger ce blocage puis de relancer /build.")
    if not parts:
        return f"Rien de nouveau à exécuter. Le plan est déjà à jour dans {plan_label}."
    parts.append(f"Le plan est à jour dans {plan_label}.")
    return " ".join(parts)


def build_summary(result: BuildResult) -> str:
    if result.approval_pending:
        report = next((_report for _report in result.reports if _report_needs_approval(_report)), None)
        title = report.title if report is not None else "la tâche en cours"
        reason = ""
        if report is not None:
            reason = next((blocker for blocker in report.result.blockers if blocker != "approval_pending"), "")
        detail = f" pour `{title}`" if title else ""
        lines = [f"Validation requise{detail}."]
        if reason:
            lines.append(f"Raison : {reason}")
        lines.append("Autorise l'action pour reprendre le build, ou refuse-la pour que l'agent cherche une autre voie ou explique le blocage.")
        if result.plan_path is not None:
            lines.append(f"Plan : {_workspace_relative(result.plan_path)}.")
        return "\n".join(lines)
    if result.error:
        return f"Build bloqué : {result.error}"
    if result.plan_path is None:
        return "Build bloqué : aucun plan utilisable."
    if not result.reports:
        return f"Rien de nouveau à exécuter. Le plan est déjà à jour dans {_workspace_relative(result.plan_path)}."

    done = [report.title for report in result.reports if report.result.status == "done"]
    errors = [report for report in result.reports if report.result.status != "done"]
    dependency_blocked = [
        report
        for report in errors
        if _report_dependency_blocked(report)
    ]
    direct_errors = [report for report in errors if report not in dependency_blocked]
    heading = "Build terminé." if not errors else "Build bloqué."
    lines = [heading]
    if done:
        lines.append(f"Terminé : {_human_list(done)}.")
    if direct_errors:
        lines.append("En erreur : " + _human_list(report.title for report in direct_errors) + ".")
        for report in direct_errors[:3]:
            detail = _one_line(report.result.summary).rstrip(".")
            blockers = _human_blockers(report.result.blockers, _title_by_id(result.reports))
            if blockers:
                detail = f"{detail} Blocage : {_human_list(blockers)}"
            lines.append(f"- {report.title} : {detail}.")
    if dependency_blocked:
        lines.append("Bloqué par dépendance : " + _human_list(report.title for report in dependency_blocked) + ".")
    suggestion = _first_next_suggestion(errors)
    if suggestion:
        lines.append(f"Prochain pas : {suggestion}")
    elif errors:
        lines.append("Prochain pas : corriger la première tâche en erreur puis relancer `/build`.")
    lines.append(f"Plan : {_workspace_relative(result.plan_path)}.")
    return "\n".join(lines)


def build_output_metadata(result: BuildResult) -> dict[str, object]:
    return {
        "total": result.total,
        "completed_before": sorted(result.completed_before),
        "ok": result.ok,
        "has_errors": result.has_errors,
        "approval_pending": result.approval_pending,
        "tasks": [
            {
                "id": report.result.task_id,
                "title": report.title,
                "status": report.result.status,
                "summary": report.result.summary,
                "blockers": list(report.result.blockers),
                "evidence": list(report.result.evidence),
                "changed": list(report.result.changed),
                "observed": list(report.result.observed),
                "next_suggestion": report.result.next_suggestion,
                "block_categories": _trace_block_categories(report.trace),
                "trace_count": len(report.trace),
                "trace": [
                    {
                        "type": event.event_type,
                        "summary": event.summary,
                        "time": event.time,
                        "data": event.data,
                    }
                    for event in report.trace[-40:]
                ],
            }
            for report in result.reports
        ],
    }


def _trace_block_categories(trace: tuple[TraceEvent, ...]) -> list[str]:
    categories: list[str] = []
    for event in trace:
        category = str(event.data.get("block_category") or "").strip()
        if category and category not in categories:
            categories.append(category)
    return categories


def _report_needs_approval(report: BuildTaskReport) -> bool:
    blockers = {str(blocker).strip().lower() for blocker in report.result.blockers}
    if "approval_pending" in blockers:
        return True
    summary = report.result.summary.lower()
    return "validation requise" in summary or "validation guardian requise" in summary


def _report_dependency_blocked(report: BuildTaskReport) -> bool:
    if _dependency_skip_summary(report.result.summary):
        return True
    return bool(report.result.blockers) and all(blocker.startswith("dependency:") for blocker in report.result.blockers)


def _title_by_id(reports: tuple[BuildTaskReport, ...]) -> dict[str, str]:
    return {report.result.task_id: report.title for report in reports}


def _first_next_suggestion(reports: list[BuildTaskReport]) -> str:
    for report in reports:
        if report.result.next_suggestion.strip():
            return report.result.next_suggestion.strip()
    return ""


def _recap_blockers(results: list[TaskResult], title_by_id: dict[str, str]) -> str:
    blockers: list[str] = []
    for result in results:
        blockers.extend(_human_blockers(result.blockers, title_by_id))
        if not result.blockers and result.summary:
            blockers.append(_one_line(result.summary).rstrip("."))
    return _human_list(dict.fromkeys(blockers).keys())


def _human_blockers(blockers: tuple[str, ...], title_by_id: dict[str, str]) -> list[str]:
    human: list[str] = []
    for blocker in blockers:
        if blocker.startswith("dependency:"):
            dep_id = blocker.split(":", 1)[1]
            title = title_by_id.get(dep_id, dep_id)
            human.append(f"la tâche '{title}' n'est pas terminée")
        else:
            human.append(blocker)
    return human


def _human_list(items) -> str:
    values = [str(item) for item in items if str(item)]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return values[0] + " et " + values[1]
    return ", ".join(values[:-1]) + " et " + values[-1]


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _workspace_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)
