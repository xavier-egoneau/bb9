"""Direct runner for the local veille-rss skill."""

from __future__ import annotations

import subprocess
from pathlib import Path


def veille_command_from_text(text: str) -> str:
    value = " ".join(str(text or "").strip().split())
    if not value:
        return ""
    lower = value.lower()
    if lower.startswith(("/veille", "/veille--add", "/veille--list", "/veille--remove")):
        return value
    if "veille" not in lower and "news" not in lower and "actualité" not in lower and "actualite" not in lower:
        return ""
    topic = _topic_from_natural_veille(value)
    return f"/veille {topic}".strip() if topic else ""


def run_veille_rss_command(skills_dir: Path, command: str, *, timeout: float = 180.0) -> str:
    runner = skills_dir / "veille-rss" / "core" / "src" / "veille" / "watchRoutine.js"
    if not runner.is_file():
        return "Erreur /veille: skill `veille-rss` introuvable ou runner absent."
    script = """
const runner = process.argv[1];
const command = process.argv[2] || "";
const { handleWatchCommand } = require(runner);
Promise.resolve(handleWatchCommand(command))
  .then((text) => {
    process.stdout.write(String(text || ""));
    process.exit(0);
  })
  .catch((error) => {
    process.stderr.write(error && error.stack ? error.stack : String(error));
    process.exit(1);
  });
"""
    try:
        completed = subprocess.run(
            ("node", "-e", script, str(runner), command),
            cwd=str(runner.parent),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        return "Erreur /veille: Node.js est requis pour lancer le runner RSS."
    except subprocess.TimeoutExpired:
        return "Erreur /veille: délai dépassé pendant la collecte RSS ou l'enrichissement IA."
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return f"Erreur /veille: {detail or 'runner RSS échoué.'}"
    return completed.stdout.strip() or "Aucun résultat de veille."


def _topic_from_natural_veille(text: str) -> str:
    normalized = " ".join(text.replace("?", " ").replace("!", " ").split())
    lower = normalized.lower()
    markers = ("veille sur", "veille d'actualité", "veille actualité", "veille", "news sur", "news")
    for marker in markers:
        index = lower.find(marker)
        if index < 0:
            continue
        topic = normalized[index + len(marker) :].strip(" :,-")
        topic = _drop_polite_prefix(topic)
        if topic:
            return topic
    return ""


def _drop_polite_prefix(topic: str) -> str:
    words = topic.split()
    while words and words[0].lower() in {"de", "sur", "concernant", "à", "a", "propos", "stp", "svp"}:
        words = words[1:]
    return " ".join(words).strip()

