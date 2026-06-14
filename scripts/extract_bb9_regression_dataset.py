"""Extract BB9 regression SFT examples from visible history.

This script turns recurring BB9 failures into supervised examples. It does not
try to replay the whole conversation; it extracts a problematic turn, keeps the
preceding user request, and writes the response BB9 should learn to produce.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from build_bb9_agentic_dataset import SYSTEM

DEFAULT_HISTORY_DB = Path.home() / ".bb9" / "visible-history.db"
OUT_DIR = Path(__file__).resolve().parents[1] / "datasets" / "bb9-agentic-regressions"
MESSAGES_PATH = OUT_DIR / "bb9_agentic_regressions_messages.jsonl"
SHAREGPT_PATH = OUT_DIR / "bb9_agentic_regressions_sharegpt.jsonl"
README_PATH = OUT_DIR / "README.md"


@dataclass(frozen=True)
class HistoryMessage:
    rowid: int
    message_id: str
    session_id: str
    role: str
    content: str
    source: str
    project_path: str
    created_at: str


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    message_id: str
    kind: str
    title: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RegressionExample:
    id: str
    tags: tuple[str, ...]
    user: str
    assistant: str
    source_message_id: str
    source_rowid: int
    pattern: str
    evidence: str
    split: str = "train"


def main() -> None:
    args = parse_args()
    examples = extract_regressions(args.history_db, per_pattern_limit=args.per_pattern_limit)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_messages(examples)
    write_sharegpt(examples)
    write_readme(examples, args.history_db)
    print(f"wrote {len(examples)} regression examples")
    print(MESSAGES_PATH)
    print(SHAREGPT_PATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-db", type=Path, default=DEFAULT_HISTORY_DB)
    parser.add_argument("--per-pattern-limit", type=int, default=12)
    return parser.parse_args()


def extract_regressions(path: Path, *, per_pattern_limit: int = 12) -> list[RegressionExample]:
    if not path.is_file():
        return []
    messages, artifacts = load_history(path)
    by_message = {message.message_id: message for message in messages}
    previous_user = previous_user_by_message(messages)
    artifacts_by_message: dict[str, list[Artifact]] = defaultdict(list)
    for artifact in artifacts:
        artifacts_by_message[artifact.message_id].append(artifact)

    examples: list[RegressionExample] = []
    seen: set[tuple[str, str]] = set()
    pattern_counts: dict[str, int] = defaultdict(int)

    for message in messages:
        if message.role != "assistant":
            continue
        user = previous_user.get(message.message_id)
        if user is None or not user.content.strip():
            continue
        candidates = regression_candidates(message, user, artifacts_by_message.get(message.message_id, ()))
        for candidate in candidates:
            if pattern_counts[candidate.pattern] >= per_pattern_limit:
                continue
            key = (candidate.pattern, normalize_for_dedupe(candidate.user))
            if key in seen:
                continue
            seen.add(key)
            pattern_counts[candidate.pattern] += 1
            examples.append(candidate)

    for artifact in artifacts:
        message = by_message.get(artifact.message_id)
        if message is None:
            continue
        user = previous_user.get(message.message_id)
        if user is None:
            continue
        for candidate in artifact_regression_candidates(message, user, artifact):
            if pattern_counts[candidate.pattern] >= per_pattern_limit:
                continue
            key = (candidate.pattern, normalize_for_dedupe(candidate.user))
            if key in seen:
                continue
            seen.add(key)
            pattern_counts[candidate.pattern] += 1
            examples.append(candidate)

    return sorted(examples, key=lambda item: (item.pattern, item.source_rowid))


def load_history(path: Path) -> tuple[list[HistoryMessage], list[Artifact]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        messages = [
            HistoryMessage(
                rowid=int(row["id"]),
                message_id=str(row["message_id"] or ""),
                session_id=str(row["session_id"] or ""),
                role=str(row["role"] or ""),
                content=str(row["content"] or ""),
                source=str(row["source"] or ""),
                project_path=str(row["project_path"] or ""),
                created_at=str(row["created_at"] or ""),
            )
            for row in conn.execute(
                """
                SELECT id, message_id, session_id, role, content, source, project_path, created_at
                FROM visible_messages
                ORDER BY id ASC
                """
            )
        ]
        artifacts = []
        for row in conn.execute(
            """
            SELECT artifact_id, message_id, kind, title, metadata_json
            FROM artifacts
            ORDER BY id ASC
            """
        ):
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            artifacts.append(
                Artifact(
                    artifact_id=str(row["artifact_id"] or ""),
                    message_id=str(row["message_id"] or ""),
                    kind=str(row["kind"] or ""),
                    title=str(row["title"] or ""),
                    metadata=metadata,
                )
            )
    finally:
        conn.close()
    return messages, artifacts


def previous_user_by_message(messages: list[HistoryMessage]) -> dict[str, HistoryMessage]:
    previous: dict[str, HistoryMessage] = {}
    last_by_session: dict[str, HistoryMessage] = {}
    for message in messages:
        if message.role == "user":
            last_by_session[message.session_id] = message
        else:
            user = last_by_session.get(message.session_id)
            if user is not None:
                previous[message.message_id] = user
    return previous


def regression_candidates(
    message: HistoryMessage,
    user: HistoryMessage,
    artifacts: list[Artifact],
) -> list[RegressionExample]:
    text = message.content
    lower = text.lower()
    candidates: list[RegressionExample] = []

    if "subagent not found:" in lower and "project-explorer" in lower:
        candidates.append(
            make_example(
                "subagent_not_found_project_explorer",
                message,
                user,
                ("hard-case", "delegate", "worker"),
                "`project-explorer` est une capacité/tool documentaire, pas un worker. Corrige le plan avec `worker: default` ou un subagent réellement présent, puis mets l'exploration dans le `goal` ou le `context` de la tâche.",
                evidence=text,
            )
        )
    if "subagent not found:" in lower and "/default" in lower:
        candidates.append(
            make_example(
                "subagent_not_found_default",
                message,
                user,
                ("hard-case", "delegate", "worker"),
                "Le bon comportement est d'utiliser le fallback worker `default` éphémère si aucune archive `default` n'existe. Vérifie la résolution des workers au niveau du pool plat, puis relance `/build`; ne demande pas à l'utilisateur de créer un subagent juste pour débloquer cette tâche.",
                evidence=text,
            )
        )
    if "providererror" in lower or "request timed out" in lower:
        candidates.append(
            make_example(
                "provider_timeout_wording",
                message,
                user,
                ("hard-case", "provider", "timeout"),
                "`ProviderError` est trop générique. Classe ce cas comme `model_timeout` si le message parle de timeout, même en local avec Ollama via `/v1`. Le diagnostic utile est : modèle local froid, prompt trop lourd, ou worker qui boucle trop longtemps avant de finaliser.",
                evidence=text,
            )
        )
    if "## bilan du projet" in lower and ("tools index" in lower or "skills index" in lower):
        candidates.append(
            make_example(
                "project_summary_mentions_internal_indexes",
                message,
                user,
                ("hard-case", "project-summary", "context"),
                "BB9_ACTION shell git status --short && find . -maxdepth 2 -type f | sort | head -80",
                evidence=text,
            )
        )
    if text.strip() == "Plan prêt." and user.content.strip().startswith("/plan"):
        candidates.append(
            make_example(
                "plan_ready_needs_executable_plan",
                message,
                user,
                ("hard-case", "plan"),
                plan_expected_for(user.content),
                evidence=text,
            )
        )
    if "ollama request timed out" in lower:
        candidates.append(
            make_example(
                "ollama_timeout_local",
                message,
                user,
                ("hard-case", "ollama", "provider"),
                "Un timeout Ollama local signifie que le serveur ou le modèle n'a pas répondu dans le délai HTTP. Vérifie d'abord que le modèle est chargé (`ollama ps`), puis réduis le contexte ou le budget worker si le timeout arrive pendant `/build`.",
                evidence=text,
            )
        )
    return candidates


def artifact_regression_candidates(
    message: HistoryMessage,
    user: HistoryMessage,
    artifact: Artifact,
) -> list[RegressionExample]:
    raw = json.dumps(artifact.metadata, ensure_ascii=False).lower()
    candidates: list[RegressionExample] = []
    if "unrecognized flag --files-from" in raw:
        candidates.append(
            make_example(
                "shell_invalid_rg_files_from",
                message,
                user,
                ("hard-case", "shell", "recovery"),
                "BB9_ACTION shell rg --files | rg '(^|/)(package.json|pyproject.toml|requirements.txt|Pipfile|uv.lock)$'",
                evidence=artifact.title + "\n" + raw[:1200],
            )
        )
    if "mutating find option is not read-only" in raw or "find option is not read-only" in raw:
        candidates.append(
            make_example(
                "shell_find_exec_blocked",
                message,
                user,
                ("hard-case", "shell", "recovery"),
                "BB9_ACTION shell find . -maxdepth 3 -type f | grep -E '(^|/)(package.json|pyproject.toml|requirements.txt|Pipfile|uv.lock)$' | sort",
                evidence=artifact.title + "\n" + raw[:1200],
            )
        )
    if "unsupported compound shell command" in raw:
        candidates.append(
            make_example(
                "shell_unsupported_compound",
                message,
                user,
                ("hard-case", "shell", "protocol"),
                "BB9_ACTION shell git status --short && find . -maxdepth 2 -type f | sort | head -80",
                evidence=artifact.title + "\n" + raw[:1200],
            )
        )
    if "providererror" in raw or "request timed out" in raw:
        candidates.append(
            make_example(
                "artifact_provider_timeout",
                message,
                user,
                ("hard-case", "provider", "delegate"),
                "`ProviderError` dans un artefact `/build` doit devenir un blocage typé `model_timeout` si le détail parle de timeout. Le prochain pas est de réduire le scope de la tâche ou d'augmenter le timeout local, pas d'accuser un tool fichier.",
                evidence=artifact.title + "\n" + raw[:1200],
            )
        )
    if "subagent not found:" in raw and "project-explorer" in raw:
        candidates.append(
            make_example(
                "artifact_project_explorer_worker",
                message,
                user,
                ("hard-case", "delegate", "worker"),
                "`project-explorer` ne doit pas apparaître dans `worker:`. Utilise `worker: default` et demande l'exploration via le goal/context de la tâche.",
                evidence=artifact.title + "\n" + raw[:1200],
            )
        )
    return candidates


def make_example(
    pattern: str,
    message: HistoryMessage,
    user: HistoryMessage,
    tags: tuple[str, ...],
    assistant: str,
    *,
    evidence: str,
) -> RegressionExample:
    return RegressionExample(
        id=f"{pattern}_{message.rowid}",
        tags=("regression", *tags),
        user=clean_user_text(user.content),
        assistant=assistant.strip(),
        source_message_id=message.message_id,
        source_rowid=message.rowid,
        pattern=pattern,
        evidence=summarize_evidence(evidence),
    )


def plan_expected_for(user_text: str) -> str:
    objective = user_text.removeprefix("/plan").strip() or "Améliorer le projet courant."
    return (
        "# BB9 Plan\n\n"
        f"Objective: {objective}\n\n"
        "## Tasks\n\n"
        "- [ ] T1 Appliquer une évolution concrète\n"
        "  worker: default\n"
        "  parallelizable: false\n"
        "  paths: bb9,docs,tests\n"
        "  depends:\n"
        "  max_iterations: 4\n"
        "  goal: Transformer la demande en changement ou vérification directement exécutable.\n"
        "  context: Le plan est le livrable de cadrage; ne pas créer une tâche qui consiste seulement à faire un autre plan.\n"
        "  expected: Patch, vérification ou diagnostic concret avec fichiers concernés.\n"
    )


def clean_user_text(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= 2000:
        return stripped
    return stripped[:2000].rstrip() + "\n...[truncated]"


def summarize_evidence(text: str) -> str:
    return " ".join(text.split())[:800]


def normalize_for_dedupe(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())[:240]


def write_messages(examples: list[RegressionExample]) -> None:
    with MESSAGES_PATH.open("w", encoding="utf-8") as handle:
        for example in examples:
            payload = {
                "id": example.id,
                "split": example.split,
                "tags": list(example.tags),
                "source_message_id": example.source_message_id,
                "source_rowid": example.source_rowid,
                "pattern": example.pattern,
                "evidence": example.evidence,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": training_user_content(example)},
                    {"role": "assistant", "content": example.assistant},
                ],
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_sharegpt(examples: list[RegressionExample]) -> None:
    with SHAREGPT_PATH.open("w", encoding="utf-8") as handle:
        for example in examples:
            payload = {
                "id": example.id,
                "split": example.split,
                "tags": list(example.tags),
                "source_message_id": example.source_message_id,
                "source_rowid": example.source_rowid,
                "pattern": example.pattern,
                "evidence": example.evidence,
                "conversations": [
                    {"from": "system", "value": SYSTEM},
                    {"from": "human", "value": training_user_content(example)},
                    {"from": "gpt", "value": example.assistant},
                ],
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def training_user_content(example: RegressionExample) -> str:
    original = example.user.strip()
    if not needs_observed_failure_context(example):
        return original
    return (
        "Cas de régression BB9.\n\n"
        "## Demande utilisateur originale\n"
        f"{original}\n\n"
        "## Mauvais comportement observé\n"
        f"{example.evidence}\n\n"
        "Réponds avec le comportement attendu que BB9 devrait apprendre pour éviter cette erreur."
    )


def needs_observed_failure_context(example: RegressionExample) -> bool:
    return example.pattern not in {"project_summary_mentions_internal_indexes", "plan_ready_needs_executable_plan"}


def write_readme(examples: list[RegressionExample], history_db: Path) -> None:
    pattern_counts: dict[str, int] = defaultdict(int)
    for example in examples:
        pattern_counts[example.pattern] += 1
    lines = [
        "# BB9 Agentic Regression Dataset",
        "",
        "Dataset extrait de l'historique visible BB9 pour transformer des erreurs",
        "récurrentes en exemples SFT attendus.",
        "",
        f"Source : `{history_db}`",
        f"Total : `{len(examples)}` exemples.",
        "",
        "## Fichiers",
        "",
        "- `bb9_agentic_regressions_messages.jsonl` : format JSONL `messages`.",
        "- `bb9_agentic_regressions_sharegpt.jsonl` : format JSONL `conversations`.",
        "",
        "## Patterns",
        "",
    ]
    if pattern_counts:
        lines.extend(f"- `{pattern}` : {count}" for pattern, count in sorted(pattern_counts.items()))
    else:
        lines.append("- Aucun pattern extrait.")
    lines.extend(
        [
            "",
            "## Usage",
            "",
            "Mélanger avec le seed dataset BB9. Les exemples portent `tags` et",
            "`pattern`, ce qui permet d'oversampler les cas difficiles sans les",
            "confondre avec les contrats de base.",
            "",
            "```python",
            "from datasets import load_dataset, concatenate_datasets",
            "",
            "seed = load_dataset('json', data_files='datasets/bb9-agentic-qwen3/bb9_agentic_qwen3_messages.jsonl')['train']",
            "reg = load_dataset('json', data_files='datasets/bb9-agentic-regressions/bb9_agentic_regressions_messages.jsonl')['train']",
            "train = concatenate_datasets([seed, reg])",
            "```",
            "",
        ]
    )
    README_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
