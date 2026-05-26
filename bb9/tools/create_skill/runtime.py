"""Create user skill skeletons."""

from __future__ import annotations

import os
import re
from pathlib import Path

from bb9.core.models import Action, GuardianDecision, Observation, RunContext


USER_CONFIG_DIR = Path(os.environ.get("BB9_HOME", Path.home() / ".bb9")).expanduser()
USER_SKILLS_DIR = USER_CONFIG_DIR / "skills"
LOCAL_SKILLS_RELATIVE_DIR = Path(".bb9") / "skills"


def action_from_text(text: str) -> Action:
    parts = text.strip().split()
    if len(parts) >= 2 and parts[0].lower() in {"draft", "create"}:
        try:
            name = normalize_skill_name(parts[1])
        except ValueError:
            return Action(name="create_skill", params={"op": "invalid", "raw": text}, risk="forbidden")
        flags = {part.lower() for part in parts[2:]}
        scope = parse_scope(flags)
        with_cli = bool(flags & {"cli", "repl", "command", "commands"})
        with_runtime = bool(flags & {"runtime", "action", "actions"})
        with_core = "core" in flags
        return Action(
            name="create_skill",
            params={
                "op": "draft",
                "name": name,
                "scope": scope,
                "with_cli": with_cli,
                "with_runtime": with_runtime,
                "with_core": with_core,
            },
            risk="medium",
        )
    return Action(name="create_skill", params={"op": "invalid", "raw": text}, risk="forbidden")


def review(action: Action, _: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", "")).strip().lower()
    if op == "draft":
        scope = normalize_scope(str(action.params.get("scope", "global")))
        reason = f"creating a {scope} user skill requires confirmation"
        return GuardianDecision(verdict="ask", reason=reason, action=action)
    return GuardianDecision(verdict="block", reason="invalid create_skill action", action=action)


def execute(action: Action) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    if op != "draft":
        return Observation(ok=False, summary="Invalid create_skill operation.")

    name = normalize_skill_name(str(action.params.get("name", "")))
    scope = normalize_scope(str(action.params.get("scope", "global")))
    with_cli = bool(action.params.get("with_cli", False))
    with_runtime = bool(action.params.get("with_runtime", False))
    with_core = bool(action.params.get("with_core", False))
    skills_dir = skills_root_for_scope(scope)
    skill_dir = skills_dir / name
    skill_file = skill_dir / "SKILL.md"
    cli_file = skill_dir / "cli.py"
    runtime_file = skill_dir / "runtime.py"
    core_file = skill_dir / "core.py"

    if skill_file.exists():
        return Observation(ok=False, summary=f"Skill already exists: {skill_file}")

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(
        skill_template(name, scope=scope, with_cli=with_cli, with_runtime=with_runtime, with_core=with_core),
        encoding="utf-8",
    )
    created = [str(skill_file)]

    if with_cli:
        cli_file.write_text(cli_template(name), encoding="utf-8")
        created.append(str(cli_file))
    if with_runtime:
        runtime_file.write_text(runtime_template(name), encoding="utf-8")
        created.append(str(runtime_file))
    if with_core:
        core_file.write_text(core_template(name), encoding="utf-8")
        created.append(str(core_file))

    summary = f"{scope.title()} skill draft created:\n" + "\n".join(f"- {path}" for path in created)
    return Observation(
        ok=True,
        summary=summary,
        data={"skill": name, "scope": scope, "root": str(skills_dir), "created": tuple(created)},
    )


def normalize_skill_name(name: str) -> str:
    text = name.strip().lower().replace("_", "-").replace(" ", "-")
    text = re.sub(r"[^a-z0-9-]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if not text:
        raise ValueError("empty skill name")
    return text


def parse_scope(flags: set[str]) -> str:
    if flags & {"local", "workspace", "project", "projet"}:
        return "local"
    if flags & {"global", "user", "utilisateur"}:
        return "global"
    return "global"


def normalize_scope(scope: str) -> str:
    text = scope.strip().lower()
    if text in {"local", "workspace", "project", "projet"}:
        return "local"
    return "global"


def skills_root_for_scope(scope: str) -> Path:
    if normalize_scope(scope) == "local":
        return Path.cwd() / LOCAL_SKILLS_RELATIVE_DIR
    return USER_SKILLS_DIR


def skill_template(name: str, *, scope: str, with_cli: bool, with_runtime: bool, with_core: bool) -> str:
    title = name.replace("-", " ").title()
    cli_note = (
        f"- `/{name}` : commande utilisateur explicite via `cli.py`, avec sortie lisible en langage naturel."
        if with_cli
        else "- Aucune commande REPL au départ."
    )
    runtime_note = (
        f"`BB9_ACTION {name} ...` via `runtime.py`."
        if with_runtime
        else "Aucune action runtime au départ."
    )
    core_note = "`core.py` contient du backend partagé." if with_core else "Aucun backend Python au départ."
    scope_note = (
        "Skill local au workspace courant. Il prend le dessus sur un skill global du même nom."
        if normalize_scope(scope) == "local"
        else "Skill global utilisateur. Un workspace peut le surcharger avec `.bb9/skills/<name>/`."
    )
    return f"""# {title}

## Résumé

Décrire en une phrase ce que ce skill ajoute à BB9.

## Activation

on-demand

## Portée

{scope_note}

## Intention

Décrire le résultat recherché pour l'utilisateur.

## Quand l'utiliser

- Ajouter des signaux concrets.
- Éviter les conditions floues.

## Comportement attendu

- Rester minimal et explicite.
- Utiliser les tools existants avant de demander une nouvelle capacité.
- Demander validation avant toute écriture durable.
- Laisser l'agent transformer les observations techniques en réponse naturelle.

## Tools utilisés

- `shell` si une lecture locale est nécessaire.

## Commandes

{cli_note}

Convention recommandée :

- `/{name}` pour la commande principale du skill ;
- `/{name}-<action>` pour les variantes ;
- éviter les alias courts non namespacés comme `/maj`, `/run` ou `/review`.

Les commandes propres à ce skill vivent dans cette archive. Utiliser `cli.py`
seulement si une intégration REPL humaine réelle est nécessaire.

Ne pas créer de commande REPL uniquement pour exposer plus vite une action à
l'utilisateur. Pour une capacité d'agent, préférer `runtime.py` et
`BB9_ACTION {name} ...`.

## Actions

{runtime_note}

Décrire les actions que le skill peut proposer.

Préférer :

```text
BB9_ACTION <tool> <arguments>
```

pour utiliser un tool existant.

Les observations runtime sont techniques et destinées à l'agent. L'utilisateur
doit recevoir un bilan naturel rédigé par l'agent.

## Permissions

- Lecture locale : selon le guardian.
- Écriture durable : demande confirmation.
- Secrets : utiliser uniquement des références `secret:NOM`.

## Portabilité

- Pas de chemin absolu machine.
- Pas de secret brut.
- Pas de dépendance implicite au workspace courant.

## Backend

{core_note}

## Tests manuels

- Lancer `bb9`.
- Vérifier `/context`.
- Vérifier que le comportement attendu est visible dans une tâche simple.
"""


def cli_template(name: str) -> str:
    command = "/" + name
    return f'''"""REPL entrypoint for the {name} skill."""

from __future__ import annotations


def register(cli) -> None:
    cli.add_command("{command}", lambda rest: _run(cli, rest), "commande du skill {name}")


def _run(cli, rest: str) -> bool:
    print("Commande {name} terminée.")
    return True
'''


def runtime_template(name: str) -> str:
    return f'''"""Runtime entrypoint for the {name} skill."""

from __future__ import annotations

from bb9.core.models import Action, Observation


def action_from_text(text: str) -> Action:
    return Action(name="{name}", params={{"text": text.strip()}}, risk="medium")


def execute(action: Action) -> Observation:
    return Observation(ok=False, summary="Skill {name}: runtime a completer.")
'''


def core_template(name: str) -> str:
    return f'''"""Backend helpers for the {name} skill."""

from __future__ import annotations


def summary() -> str:
    return "Skill {name} backend"
'''
