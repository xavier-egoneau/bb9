"""Create user skill skeletons."""

from __future__ import annotations

import os
import re
from pathlib import Path

from bb9.core.models import Action, GuardianDecision, Observation, RunContext


USER_CONFIG_DIR = Path(os.environ.get("BB9_HOME", Path.home() / ".bb9")).expanduser()
USER_SKILLS_DIR = USER_CONFIG_DIR / "skills"


def action_from_text(text: str) -> Action:
    parts = text.strip().split()
    if len(parts) >= 2 and parts[0].lower() in {"draft", "create"}:
        try:
            name = normalize_skill_name(parts[1])
        except ValueError:
            return Action(name="create_skill", params={"op": "invalid", "raw": text}, risk="forbidden")
        with_cli = any(part.lower() in {"cli", "repl", "command", "commands"} for part in parts[2:])
        return Action(name="create_skill", params={"op": "draft", "name": name, "with_cli": with_cli}, risk="medium")
    return Action(name="create_skill", params={"op": "invalid", "raw": text}, risk="forbidden")


def review(action: Action, _: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", "")).strip().lower()
    if op == "draft":
        return GuardianDecision(verdict="ask", reason="creating a user skill requires confirmation", action=action)
    return GuardianDecision(verdict="block", reason="invalid create_skill action", action=action)


def execute(action: Action) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    if op != "draft":
        return Observation(ok=False, summary="Invalid create_skill operation.")

    name = normalize_skill_name(str(action.params.get("name", "")))
    with_cli = bool(action.params.get("with_cli", False))
    skill_dir = USER_SKILLS_DIR / name
    skill_file = skill_dir / "SKILL.md"
    cli_file = skill_dir / "cli.py"

    if skill_file.exists():
        return Observation(ok=False, summary=f"Skill already exists: {skill_file}")

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(skill_template(name, with_cli=with_cli), encoding="utf-8")
    created = [str(skill_file)]

    if with_cli:
        cli_file.write_text(cli_template(name), encoding="utf-8")
        created.append(str(cli_file))

    summary = "Skill draft created:\n" + "\n".join(f"- {path}" for path in created)
    return Observation(ok=True, summary=summary, data={"skill": name, "created": tuple(created)})


def normalize_skill_name(name: str) -> str:
    text = name.strip().lower().replace("_", "-").replace(" ", "-")
    text = re.sub(r"[^a-z0-9-]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if not text:
        raise ValueError("empty skill name")
    return text


def skill_template(name: str, *, with_cli: bool) -> str:
    title = name.replace("-", " ").title()
    cli_note = f"`/{name}`" if with_cli else "Aucune commande REPL au départ."
    return f"""# {title}

## Résumé

Décrire en une phrase ce que ce skill ajoute à BB9.

## Activation

on-demand

## Intention

Décrire le résultat recherché pour l'utilisateur.

## Quand l'utiliser

- Ajouter des signaux concrets.
- Éviter les conditions floues.

## Comportement attendu

- Rester minimal et explicite.
- Utiliser les tools existants avant de demander une nouvelle capacité.
- Demander validation avant toute écriture durable.

## Tools utilisés

- `shell` si une lecture locale est nécessaire.

## Commandes REPL

{cli_note}

## Actions

Décrire les actions que le skill peut proposer.

Préférer :

```text
BB9_ACTION <tool> <arguments>
```

pour utiliser un tool existant.

## Permissions

- Lecture locale : selon le guardian.
- Écriture durable : demande confirmation.
- Secrets : utiliser uniquement des références `secret:NOM`.

## Portabilité

- Pas de chemin absolu machine.
- Pas de secret brut.
- Pas de dépendance implicite au workspace courant.

## Tests manuels

- Lancer `bb9`.
- Vérifier `/context`.
- Vérifier que le comportement attendu est visible dans une tâche simple.
"""


def cli_template(name: str) -> str:
    command = "/" + name
    return f'''"""REPL extension for the {name} skill."""

from __future__ import annotations


def register(cli) -> None:
    cli.add_command("{command}", lambda rest: _run(cli, rest), "commande du skill {name}")


def _run(cli, rest: str) -> bool:
    print("Skill {name}: " + (rest or "ok"))
    return True
'''
