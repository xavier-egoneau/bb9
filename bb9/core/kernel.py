"""Lightweight decision entry point."""

from __future__ import annotations

import logging
import re
import shlex
import unicodedata

from bb9.providers.providers import Provider

from .attachments import image_context_block, resolve_image_attachments, strip_image_refs
from .markdown import command_aliases
from .models import Action, Decision, Intention, RunContext
from .paths import default_system_prompt_path
from .tool_runtime import runtime_action_from_text

ACTION_PREFIX = "BB9_ACTION"
MAX_OBSERVATION_CHARS = 12000
ACTION_LINE_RE = re.compile(rf"(?m)^\s*{re.escape(ACTION_PREFIX)}\b")

_logger = logging.getLogger("bb9.kernel")

_SYSTEM_PROMPT_CACHE: str | None = None


def _load_system_prompt() -> str:
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE is not None:
        return _SYSTEM_PROMPT_CACHE
    try:
        path = default_system_prompt_path()
        if path.is_file():
            _SYSTEM_PROMPT_CACHE = path.read_text(encoding="utf-8").strip()
            return _SYSTEM_PROMPT_CACHE
    except Exception:
        _logger.warning("Failed to load system prompt from %s, using fallback.", path)
    _SYSTEM_PROMPT_CACHE = FALLBACK_SYSTEM_PROMPT
    return _SYSTEM_PROMPT_CACHE


FALLBACK_SYSTEM_PROMPT = (
    "Tu es BB9, un systeme agentique minimal. "
    "Reponds dans la langue de l'utilisateur. "
    "Reste concis, utile et explicite sur les limites. "
    "Utilise BB9_ACTION pour demander des actions. "
    "Ne recopie pas les observations techniques a l'utilisateur ; fais un bilan naturel."
)


class Kernel:
    def __init__(self, provider: Provider | None = None) -> None:
        self._provider = provider

    def decide(self, intention: Intention, context: RunContext) -> Decision:
        text = intention.text.strip()
        if text.startswith("/action "):
            body = text.removeprefix("/action ").strip()
            runtime_decision = _runtime_decision_from_body(body, context)
            if runtime_decision is not None:
                return runtime_decision

            name = body.strip()
            if not name:
                return Decision(kind="stop", summary="Missing action name.")
            return Decision(
                kind="action",
                summary=f"Request action: {name}",
                action=Action(name=name, risk="forbidden"),
            )

        if _is_context_inventory_question(text):
            return Decision(kind="answer", summary=_context_inventory_answer(context))

        if self._provider is not None:
            images = resolve_image_attachments(text, context.workspace.root)
            provider_text = strip_image_refs(text) if images else text
            prompt = self._build_prompt(
                provider_text,
                context,
                tool_observations=tuple(intention.metadata.get("tool_observations", ())),
                tool_limit_reached=bool(intention.metadata.get("tool_limit_reached", False)),
                image_context=image_context_block(images),
            )
            return self._decision_from_provider_output(self._provider.complete(prompt, images=images), context)

        return Decision(kind="answer", summary=text)

    def _build_prompt(
        self,
        text: str,
        context: RunContext,
        *,
        tool_observations: tuple[dict[str, str], ...] = (),
        tool_limit_reached: bool = False,
        image_context: str = "",
    ) -> str:
        prompt_parts = [
            "# BB9 runtime context",
            _load_system_prompt(),
        ]
        agent_behavior = self.agent_behavior_context(context)
        if agent_behavior:
            prompt_parts.append(agent_behavior)
        prompt_parts.append(self.autonomy_context(context))
        if context.agent is not None:
            prompt_parts.append(context.agent.as_prompt_context())
        session_context = context.session.as_prompt_context()
        if session_context.strip():
            prompt_parts.append(session_context)
        if context.workspace_status.strip():
            prompt_parts.append(context.workspace_status.strip())
        if context.context_index.strip():
            prompt_parts.append(context.context_index.strip())
        if context.notes_context.strip():
            prompt_parts.append(context.notes_context.strip())
        if context.subagents_index.strip():
            prompt_parts.append(context.subagents_index.strip())
        if context.skills_index.strip():
            prompt_parts.append(context.skills_index.strip())
        if context.tools_index.strip():
            prompt_parts.append(context.tools_index.strip())
        prompt_parts.append(self.provider_action_protocol_context())
        for skill in context.skills:
            if skill.activation == "always" or _intention_matches_skill(text, skill.name, skill.commands, skill.activation):
                prompt_parts.append(skill.as_prompt_context())
        if tool_observations:
            prompt_parts.append(self._tool_observations_context(tool_observations))
        if image_context.strip():
            prompt_parts.append(image_context.strip())
        if tool_limit_reached:
            prompt_parts.append(
                "# Instruction interne de finalisation\n\n"
                "Ne demande plus de BB9_ACTION dans ce tour. "
                "Produis maintenant la meilleure reponse possible avec les observations disponibles. "
                "Ne mentionne pas la limite interne de tools a l'utilisateur."
            )
        prompt_parts.append(
            "# Frontiere de tour\n\n"
            "L'intention courante ci-dessous est l'autorite de ce tour. "
            "La session recente sert seulement de contexte. "
            "Si l'utilisateur change de sujet ou utilise une commande slash, ne continue pas la tache precedente, "
            "sauf demande explicite de continuer. "
            "Une reponse finale doit satisfaire explicitement l'intention courante."
        )
        prompt_parts.append(
            "# Frontiere agent/projet\n\n"
            "Les sections Tools Index, Skills Index, Subagents Index, contexte d'identite, budget de contexte "
            "et protocole BB9_ACTION decrivent tes moyens de travail, pas le projet analyse. "
            "Pour un bilan, une critique, une analyse ou un etat du repo/projet, concentre-toi sur le workspace : "
            "fichiers, code, docs, tests, git status, configuration projet et observations de tools. "
            "Ne presente pas les tools, skills, subagents, budgets ou reglages internes comme des caracteristiques "
            "du projet, sauf si l'utilisateur demande explicitement un bilan de BB9, de l'agent ou de ses capacites."
        )
        prompt_parts.append(f"# Intention courante\n\n{text}")
        return "\n\n".join(prompt_parts)

    def _decision_from_provider_output(self, output: str, context: RunContext) -> Decision:
        text = output.strip()
        action_body = _last_action_body(text)
        if action_body is not None:
            body = action_body
            if body.startswith(":"):
                body = body[1:].strip()
            body = _strip_action_markup(body)
            if _contains_nested_action_prefix(body):
                repaired_body = _single_files_read_from_nested_actions(body)
                if repaired_body is None:
                    return _invalid_provider_action(body)
                body = repaired_body
            body = _normalize_action_body(body)
            if _looks_like_placeholder_action(body):
                answer = _without_action_lines(text)
                if answer:
                    return Decision(kind="answer", summary=answer)
                return Decision(kind="answer", summary="Action ignoree: demande de tool placeholder ou incomplete.")
            runtime_decision = _runtime_decision_from_body(body, context)
            if runtime_decision is not None:
                return runtime_decision
            return _invalid_provider_action(body)
        return Decision(kind="answer", summary=text)

    def _tool_observations_context(self, observations: tuple[dict[str, str], ...]) -> str:
        parts = ["# Observations tools"]
        for index, observation in enumerate(observations, 1):
            tool = observation.get("tool", "")
            cmd = observation.get("cmd", "")
            ok = observation.get("ok", "")
            output = _truncate(observation.get("output", ""), MAX_OBSERVATION_CHARS)
            parts.append(f"## Observation {index}\n\ntool: {tool}\ncmd: {cmd}\nok: {ok}\n\n```text\n{output}\n```")
        return "\n\n".join(parts)

    def autonomy_context(self, context: RunContext) -> str:
        profile = context.permission_profile
        if profile == "power":
            behavior = (
                "Profil actif: power. Sois proactif dans le workspace et les trusted roots. "
                "Quand une information manque pour accomplir l'intention, demande directement la prochaine lecture utile avec BB9_ACTION. "
                "Ne transforme pas une lecture utile en question de confort pour l'utilisateur. "
                "Quand tu produis un resultat visuel (UI, page web, maquette), prends un screenshot avec BB9_ACTION browser et montre-le "
                "avec ![apercu](.bb9/artifacts/screenshots/...) sans attendre que l'utilisateur le demande."
            )
        elif profile == "limited":
            behavior = (
                "Profil actif: limited. Avance de façon autonome sur les lectures et verifications courantes. "
                "Demande confirmation seulement quand le guardian l'exige ou quand l'intention est ambiguë. "
                "Quand tu produis un resultat visuel, montre-le avec un screenshot browser."
            )
        else:
            behavior = (
                "Profil actif: safe. Reste prudent, mais ne demande pas a l'utilisateur de faire les lectures a ta place. "
                "Utilise BB9_ACTION pour les lectures simples quand elles sont necessaires."
            )
        planning = (
            "\n\n# Réflexe plan\n\n"
            "Si l'intention demande plus de deux étapes, touche plusieurs fichiers, implique une feature complète, "
            "un refactor, une migration, des tests plus documentation, ou une coordination de tâches, commence par structurer le travail. "
            "Utilise le mode plan quand il est disponible au lieu d'improviser une longue suite d'actions. "
            "Si la demande est simple, ponctuelle ou clairement limitée à une ou deux étapes, agis directement sans plan de confort. "
            "N'exécute pas un plan avec `/build` sans demande explicite de l'utilisateur."
        )
        return "# Autonomie\n\n" + behavior + planning

    def agent_behavior_context(self, context: RunContext) -> str:
        if context.agent is None or not context.agent.soul.strip():
            return ""
        soul = context.agent.soul
        directives = [
            "IDENTITY.md et SOUL.md modifient tes decisions, pas seulement ton style.",
            "Quand SOUL.md demande de la debrouillardise, transforme une information manquante en action BB9_ACTION precise plutot qu'en demande a l'utilisateur.",
            "Quand SOUL.md demande de l'audace dans le workspace, explore, verifie et synthetise sans attendre une permission de confort.",
            "Les limites de SOUL.md restent actives : secrets, actions hors workspace, suppressions durables, configuration globale et actions vers l'exterieur restent prudents.",
        ]
        if _soul_mentions(soul, ("opinion", "avis", "préférence", "preference")):
            directives.append("Tu peux avoir un avis technique quand il aide la decision.")
        if _soul_mentions(soul, ("concis", "pas de blabla", "utile")):
            directives.append("Prefere les reponses utiles et directes aux formules de service.")
        excerpt = _markdown_summary(soul, limit=5)
        return "# Contrat comportemental actif\n\n" + "\n".join(f"- {line}" for line in directives) + f"\n\nExtraits SOUL.md: {excerpt}"

    def provider_action_protocol_context(self) -> str:
        return (
            "# Protocole BB9_ACTION\n\n"
            "Quand tu demandes un tool, ta reponse doit contenir une seule action `BB9_ACTION`. "
            "N'ajoute aucune prose avant ou apres l'action dans le meme message. "
            "Ne colle jamais deux `BB9_ACTION` dans une meme reponse : attends l'observation, puis demande l'action suivante. "
            "Pour `shell`, la commande doit etre du shell pur, sans phrase naturelle ajoutee. "
            "Le runtime shell n'utilise jamais `shell=True` et n'accepte que ces formes : "
            "commande simple, chaine `a && b`, `cmd || true`, pipes entre commandes de lecture connues "
            "(ls, find, rg, grep, sed, head, tail, cat, sort, wc), redirection simple `cmd > fichier`, "
            "heredoc `python3 - <<'PY' ... PY`. "
            "Pas de `;`, pas de `$(...)`, pas de pipe avec une commande d'ecriture : "
            "decoupe en plusieurs actions shell successives. "
            "Pour `files`, utilise une action structuree : `BB9_ACTION files read path=...`, "
            "`BB9_ACTION files write path=... text=\"\"\"...\"\"\"`, `BB9_ACTION files replace path=... old=\"...\" new=\"...\"` "
            "ou `BB9_ACTION files write_many [...]`. N'utilise pas de redirection shell pour ecrire un fichier. "
            "Pour modifier un fichier hors workspace ou hors trusted roots, demande quand meme `files` avec le chemin exact : "
            "le guardian demandera validation a l'utilisateur si necessaire. "
            "Pour plusieurs fichiers, prefere `BB9_ACTION files write_many ...` ou ecris un fichier par tour. "
            "Une action incomplete, une action avec prose collee ou plusieurs actions imbriquees sera bloquee."
        )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _is_context_inventory_question(text: str) -> bool:
    normalized = _normalize_text(text)
    phrases = (
        "tu as quoi en contexte",
        "tu as quoi en context",
        "t as quoi en contexte",
        "t as quoi en context",
        "qu as tu en contexte",
        "qu est ce que tu as en contexte",
        "quel contexte as tu",
        "quel est ton contexte",
        "quel est ton context",
        "c est quoi ton contexte",
        "c est quoi ton context",
        "contexte disponible",
        "montre ton contexte",
        "resume ton contexte",
    )
    return any(phrase in normalized for phrase in phrases)


def _normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(ascii_text.replace("'", " ").replace("?", " ").split())


def _soul_mentions(text: str, words: tuple[str, ...]) -> bool:
    normalized = _normalize_text(text)
    return any(_normalize_text(word) in normalized for word in words)


def _context_inventory_answer(context: RunContext) -> str:
    lines = ["J'ai en contexte actif :"]
    lines.append(f"- Profil d'autonomie : `{context.permission_profile}`.")
    if context.agent is not None:
        lines.append(f"- Agent : `{context.agent.name}`.")
        if context.agent.identity.strip():
            lines.append(f"- `IDENTITY.md` actif : {_markdown_summary(context.agent.identity)}")
        if context.agent.soul.strip():
            lines.append(f"- `SOUL.md` actif : {_markdown_summary(context.agent.soul)}")
        if context.agent.model.strip():
            lines.append(f"- `MODEL.md` actif : `{context.agent.model.strip()}`.")
        if context.agent.reasoning_effort.strip():
            lines.append(f"- `ReasoningEffort` actif : `{context.agent.reasoning_effort.strip()}`.")
    lines.append(f"- Workspace : `{context.workspace.root}`.")
    status_lines = _workspace_status_bullets(context.workspace_status, limit=8)
    if status_lines:
        lines.append("- Etat technique courant :")
        lines.extend(f"  - {line}" for line in status_lines)

    context_lines = _index_bullets(context.context_index, limit=12)
    if context_lines:
        lines.append("- Carte locale indexee du workspace :")
        lines.extend(f"  - {line}" for line in context_lines)

    subagent_names = _names_from_index(context.subagents_index)
    if subagent_names:
        lines.append("- Subagents disponibles : " + ", ".join(f"`{name}`" for name in subagent_names) + ".")

    tool_names = _names_from_specs_or_index(
        [tool.name for tool in context.tools],
        context.tools_index,
        limit=12,
    )
    if tool_names:
        lines.append("- Tools disponibles : " + ", ".join(f"`{name}`" for name in tool_names) + ".")

    skill_names = _names_from_specs_or_index(
        [skill.name for skill in context.skills],
        context.skills_index,
        limit=12,
    )
    if skill_names:
        lines.append("- Skills disponibles : " + ", ".join(f"`{name}`" for name in skill_names) + ".")

    archive_commands = _archive_commands(context)
    if archive_commands:
        lines.append("- Commandes d'archives : " + ", ".join(f"`{command}`" for command in archive_commands) + ".")

    if context.session.messages:
        lines.append(f"- Session courte : {len(context.session.messages)} message(s) recent(s).")

    lines.append(
        "Base active suffisante pour m'orienter. Pour analyser, je pars de cet index "
        "et je lis directement les fichiers pertinents via actions controlees avant d'agir."
    )
    return "\n".join(lines)


def _workspace_status_bullets(text: str, *, limit: int = 8) -> tuple[str, ...]:
    bullets: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        bullets.append(line.removeprefix("- ").strip())
        if len(bullets) >= limit:
            break
    return tuple(bullets)


def _intention_matches_skill(
    text: str,
    skill_name: str,
    commands: tuple[str, ...] = (),
    activation: str = "",
) -> bool:
    first = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    if first == f"/{skill_name.lower()}":
        return True
    if first in tuple(alias.lower() for alias in command_aliases(commands)):
        return True
    return _activation_matches_intention(text, activation)


def _activation_matches_intention(text: str, activation: str) -> bool:
    normalized = _normalize_text(text)
    first = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    for raw_trigger in activation.replace("\n", ",").split(","):
        trigger = raw_trigger.strip().strip("`")
        if not trigger or trigger in {"always", "on-demand"}:
            continue
        if trigger.startswith("/"):
            if first == trigger.lower():
                return True
            continue
        normalized_trigger = _normalize_text(trigger)
        if len(normalized_trigger) >= 4 and normalized_trigger in normalized:
            return True
    return False


def _archive_commands(context: RunContext, *, limit: int = 12) -> tuple[str, ...]:
    commands: list[str] = []
    for skill in context.skills:
        commands.extend(command_aliases(skill.commands))
    for tool in context.tools:
        commands.extend(command_aliases(tool.commands))
    result: list[str] = []
    for command in commands:
        if command not in result:
            result.append(command)
        if len(result) >= limit:
            break
    return tuple(result)


def _markdown_summary(text: str, *, limit: int = 4) -> str:
    highlights: list[str] = []
    for raw_line in text.splitlines():
        line = _clean_markdown_line(raw_line)
        if not line:
            continue
        highlights.append(line)
        if len(highlights) >= limit:
            break
    if not highlights:
        return "charge."
    summary = "; ".join(_truncate_one_line(line, 110) for line in highlights)
    if summary.endswith((".", "!", "?")):
        return summary
    return summary + "."


def _clean_markdown_line(line: str) -> str:
    line = line.strip()
    while line.startswith("#"):
        line = line[1:].strip()
    for prefix in ("-", "*"):
        if line.startswith(prefix):
            line = line.removeprefix(prefix).strip()
    line = line.strip("`*_ ")
    if not line:
        return ""
    if line.lower() in {"identity", "soul.md", "soul.md - qui tu es", "qui tu es"}:
        return ""
    return line


def _index_bullets(index: str, *, limit: int) -> list[str]:
    result: list[str] = []
    current_section = ""
    accepted_sections = {"governance", "directories", "files"}
    for raw_line in index.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_section = line.removeprefix("## ").strip().lower()
            continue
        if current_section not in accepted_sections or not line.startswith("- `"):
            continue
        item = line.strip("- ").strip()
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _names_from_specs_or_index(names: list[str], index: str, *, limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in [*names, *_names_from_index(index)]:
        clean = name.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _names_from_index(index: str) -> list[str]:
    names: list[str] = []
    for line in index.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- `") or "`" not in stripped[3:]:
            continue
        rest = stripped[3:]
        name, _, _ = rest.partition("`")
        if name:
            names.append(name)
    return names


def _truncate_one_line(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _runtime_decision_from_body(body: str, context: RunContext) -> Decision | None:
    tool_name, _, tool_text = body.partition(" ")
    tool_name = tool_name.strip().removesuffix(":")
    action = runtime_action_from_text(tool_name, tool_text.strip(), context)
    if action is None:
        return None
    summary = f"Request {tool_name}: {tool_text.strip()}".strip()
    return Decision(kind="action", summary=summary, action=action)


def _looks_like_placeholder_action(body: str) -> bool:
    text = body.strip()
    lower = text.lower()
    if not text:
        return True
    if _contains_protocol_placeholder(text):
        return True
    if "..." in text:
        return True
    return "nom_de_variable" in lower or lower.endswith(" nom") or lower == "secret add"


def _strip_action_markup(body: str) -> str:
    text = body.strip()
    if text.startswith("```"):
        text = text.removeprefix("```").strip()
        if text.startswith(("text", "markdown", "bb9")):
            _, _, text = text.partition("\n")
            text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return text.strip("`").strip()


def _normalize_action_body(body: str) -> str:
    text = body.strip()
    if text.startswith("shell "):
        command = text.removeprefix("shell ").strip()
        if _has_shell_heredoc(command):
            heredoc = _closed_shell_heredoc_prefix(command)
            return f"shell {heredoc if heredoc is not None else command}".strip()
        return text.splitlines()[0].strip()
    return text


def _has_shell_heredoc(command: str) -> bool:
    first_line = command.splitlines()[0] if command else ""
    try:
        argv = shlex.split(first_line)
    except ValueError:
        return "<<" in first_line
    return any(arg == "<<" or arg == "<<-" or arg.startswith("<<") for arg in argv)


def _closed_shell_heredoc_prefix(command: str) -> str | None:
    lines = command.splitlines()
    if len(lines) < 2:
        return None
    try:
        argv = shlex.split(lines[0])
    except ValueError:
        return None
    heredoc = _shell_heredoc_from_argv(argv)
    if heredoc is None:
        return None
    delimiter, strip_tabs = heredoc
    for index, line in enumerate(lines[1:], start=1):
        closing = line.lstrip("\t") if strip_tabs else line
        if closing == delimiter:
            return "\n".join(lines[: index + 1]).strip()
    return None


def _shell_heredoc_from_argv(argv: list[str]) -> tuple[str, bool] | None:
    for index, arg in enumerate(argv):
        if arg in {"<<", "<<-"}:
            if index + 1 >= len(argv):
                return None
            return argv[index + 1], arg == "<<-"
        if arg.startswith("<<-") and len(arg) > 3:
            return arg[3:], True
        if arg.startswith("<<") and len(arg) > 2:
            return arg[2:], False
    return None


def _contains_nested_action_prefix(body: str) -> bool:
    first_line = body.split("\n", 1)[0]
    return re.search(rf".+{re.escape(ACTION_PREFIX)}\s+[A-Za-z0-9_-]+\b", first_line) is not None


def _single_files_read_from_nested_actions(body: str) -> str | None:
    actions = _inline_action_segments(body)
    if len(actions) < 2:
        return None
    for tool_name, tool_text in actions:
        if tool_name != "files" or tool_text.split(maxsplit=1)[0].lower() != "read":
            return None
    tool_name, tool_text = actions[0]
    return f"{tool_name} {tool_text}".strip()


def _inline_action_segments(body: str) -> list[tuple[str, str]]:
    text = f"{ACTION_PREFIX} {body.strip()}"
    pattern = re.compile(rf"{re.escape(ACTION_PREFIX)}\s+([A-Za-z0-9_-]+)\b")
    matches = list(pattern.finditer(text))
    segments: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        tool_text = text[start:end].strip()
        if tool_text:
            segments.append((match.group(1), tool_text))
    return segments


def _invalid_provider_action(body: str) -> Decision:
    first_line = body.splitlines()[0] if body else ""
    _logger.warning("[invalid-provider-action] body=%r", body[:400])
    return Decision(
        kind="action",
        summary=f"Invalid provider action request: {ACTION_PREFIX} {first_line}",
        action=Action(name="invalid-provider-action", risk="forbidden"),
    )


def _last_action_body(text: str) -> str | None:
    matches = list(ACTION_LINE_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        unwrapped = stripped.lstrip("`").lstrip()
        if not unwrapped.startswith(ACTION_PREFIX):
            return None
        body = unwrapped[len(ACTION_PREFIX) :].strip()
        return body or None
    body = text[matches[-1].end() :].strip()
    if not body:
        return None
    return body


def _contains_protocol_placeholder(text: str) -> bool:
    placeholders = {
        "commande",
        "cmd",
        "nom",
        "nom_de_variable",
        "path",
        "chemin",
        "texte",
        "text",
        "old",
        "new",
        "marker",
        "url",
    }
    for match in re.finditer(r"<([^<>]+)>", text):
        value = "_".join(match.group(1).strip().lower().split())
        if value in placeholders:
            return True
    return False


def _without_action_lines(text: str) -> str:
    lines = [line for line in text.splitlines() if ACTION_PREFIX not in line]
    return "\n".join(line.strip() for line in lines if line.strip()).strip()
