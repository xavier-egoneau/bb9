"""REPL entrypoint for the dev skill."""

from __future__ import annotations

import re
import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bb9.core.agents import AgentNotFoundError, load_subagent
from bb9.core.delegation import delegate
from bb9.core.kernel import Kernel
from bb9.core.loop import run_once
from bb9.core.models import AgentProfile, Intention, PermissionProfile, RunContext, Task, TaskResult

PROFILES = {"safe", "limited", "power"}
RESULT_FIELDS = {"status", "summary", "blockers", "evidence"}


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
    try:
        plan_path = _plan_path(rest)
        plan_text = plan_path.read_text(encoding="utf-8")
        completed = completed_task_ids(plan_text)
        tasks = parse_plan(plan_text)
    except (OSError, ValueError) as exc:
        print("plan... error")
        print(f"blocker... {exc}")
        return True

    if not tasks:
        if completed:
            print(f"Rien de nouveau à exécuter. Le plan est déjà à jour dans {_workspace_relative(plan_path)}.")
            return True
        print("plan... error")
        print("blocker... no task found")
        return True

    print(f"plan... {len(tasks)} task(s)")
    title_by_id = {task.id: task.title for task in tasks}
    results: dict[str, TaskResult] = {}
    for task_id in completed:
        results[task_id] = TaskResult(task_id=task_id, status="done", summary="Already checked in plan.")
    pending = list(tasks)
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
            _print_result(result, task.title, title_by_id)
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
                _print_result(result, task.title, title_by_id)
            break

        parallel_group = _parallel_group(ready)
        if len(parallel_group) > 1:
            print("parallel... " + _human_list(task.title for task in parallel_group))
            for result in _execute_parallel(cli, parallel_group):
                results[result.task_id] = result
                if result.status == "done":
                    mark_task_done(plan_path, result.task_id)
                write_task_state(plan_path, result.task_id, result)
                _print_result(result, title_by_id.get(result.task_id, result.task_id), title_by_id)
            ran = {task.id for task in parallel_group}
        else:
            task = ready[0]
            result = _execute_task(cli, task)
            results[task.id] = result
            if result.status == "done":
                mark_task_done(plan_path, task.id)
            write_task_state(plan_path, task.id, result)
            _print_result(result, task.title, title_by_id)
            ran = {task.id}
        pending = [task for task in pending if task.id not in ran and task.id not in results]

    executed = [result for task_id, result in results.items() if task_id not in completed]
    print(_recap(executed, title_by_id, plan_path))
    return True


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


def _execute_parallel(cli, tasks: list[Task]) -> list[TaskResult]:
    results_by_id: dict[str, TaskResult] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {executor.submit(_execute_task, cli, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                results_by_id[task.id] = future.result()
            except Exception as exc:
                results_by_id[task.id] = TaskResult(
                    task_id=task.id,
                    status="error",
                    summary=f"Parallel task failed: {exc}",
                    blockers=(exc.__class__.__name__,),
                )
    return [results_by_id[task.id] for task in tasks]


def _execute_task(cli, task: Task) -> TaskResult:
    try:
        subagent = _load_worker(cli, task.suggested_worker or "default")
        parent_context = cli.build_context()
    except AgentNotFoundError as exc:
        return TaskResult(
            task_id=task.id,
            status="error",
            summary="Worker not available.",
            blockers=(str(exc),),
        )

    print(f"task... {task.title}: start {subagent.name}")
    return delegate(
        task,
        subagent,
        parent_context,
        lambda intention, context: _run_subagent(cli, intention, context),
    )


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
    return tuple(
        _task_from_checkbox_block(task_id, title, lines)
        for task_id, title, done, lines in blocks
        if not done
    )


def completed_task_ids(text: str) -> set[str]:
    completed: set[str] = set()
    for line in text.splitlines():
        parsed = _checkbox_task(line)
        if parsed is None:
            continue
        task_id, _, done = parsed
        if done:
            completed.add(task_id)
    return completed


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
            if "=" not in token:
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


def _print_result(result: TaskResult, title: str, title_by_id: dict[str, str]) -> None:
    label = title or title_by_id.get(result.task_id, result.task_id)
    print(f"task... {label}: {result.status}")
    print(f"sum... {result.summary}")
    if result.changed:
        print("chg... " + ", ".join(result.changed))
    if result.observed:
        print("obs... " + ", ".join(result.observed))
    if result.blockers:
        print("blk... " + "; ".join(_human_blockers(result.blockers, title_by_id)))
    if result.evidence:
        print("evd... " + " | ".join(result.evidence[:3]))
    if result.next_suggestion:
        print(f"nxt... {result.next_suggestion}")


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
