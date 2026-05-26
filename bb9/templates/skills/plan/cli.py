"""REPL entrypoint for the plan skill."""

from __future__ import annotations

from pathlib import Path

from bb9.core.channels import intention_from_text
from bb9.core.kernel import Kernel
from bb9.core.loop import run_once


PLAN_PATH = Path(".bb9") / "plan.md"


def register(cli) -> None:
    cli.add_command("/plan", lambda rest: _run(cli, rest), "produire le plan courant")


def _run(cli, rest: str) -> bool:
    objective = rest.strip()
    if not objective:
        print("plan... error")
        print("blocker... objectif manquant")
        return True

    context = cli.build_context()
    prompt = _plan_prompt(objective)
    result = run_once(
        Kernel(provider=cli.build_provider()),
        intention_from_text(prompt),
        context,
        ask_user=cli.ask_guardian,
    )
    summary = result.observation.summary if result.observation is not None else result.decision.summary
    plan = _normalize_plan(summary, objective)
    path = Path.cwd() / PLAN_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan, encoding="utf-8")
    print(f"plan... {path}")
    print("plan... écrit")
    return True


def _plan_prompt(objective: str) -> str:
    return (
        "/plan "
        + objective
        + "\n\n"
        + "Produis uniquement le contenu Markdown de `.bb9/plan.md`.\n"
        + "Le fichier doit commencer par `# BB9 Plan`.\n"
        + "Utilise ce format exact pour les tâches :\n\n"
        + "- [ ] T1 Titre court\n"
        + "  worker: default\n"
        + "  parallelizable: false\n"
        + "  paths: chemin/concerné.md\n"
        + "  depends:\n"
        + "  goal: Objectif autonome.\n"
        + "  context: Contexte suffisant pour le subagent.\n"
        + "  expected: Résultat attendu.\n\n"
        + "N'utilise pas de JSON. N'ajoute pas de commentaire hors Markdown."
    )


def _normalize_plan(text: str, objective: str) -> str:
    content = text.strip()
    if "# BB9 Plan" not in content.splitlines()[:3]:
        content = f"# BB9 Plan\n\nObjective: {objective}\n\n## Tasks\n\n{content}"
    return content.rstrip() + "\n"
