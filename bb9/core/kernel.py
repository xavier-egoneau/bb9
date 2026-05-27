"""Lightweight decision entry point."""

from __future__ import annotations

import unicodedata
import re

from .attachments import image_context_block, resolve_image_attachments, strip_image_refs
from .markdown import command_aliases
from .models import Action, Decision, Intention, RunContext
from .providers import Provider
from .tool_runtime import runtime_action_from_text


ACTION_PREFIX = "BB9_ACTION"
MAX_OBSERVATION_CHARS = 12000


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
            return self._decision_from_provider_output(_provider_complete(self._provider, prompt, images), context)

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
            (
                "Tu es BB9, un systeme agentique minimal. "
                "Reponds dans la langue de l'utilisateur. "
                "Reste concis, utile et explicite sur les limites. "
                "IDENTITY.md et SOUL.md, quand ils sont fournis, sont ton contexte d'identite actif. "
                "Ils ne sont pas decoratifs : applique leur posture dans tes choix, ton niveau d'initiative et ton ton. "
                "Si l'utilisateur te demande ce que tu as en contexte, mentionne aussi les elements utiles de ton identite et de ta posture. "
                "Les tools listes sont disponibles conceptuellement, mais tu ne les executes pas directement. "
                "Si le Tools Index marque un tool comme `unavailable`, ne l'appelle pas pour verifier sa disponibilite ; "
                "reponds depuis ce statut et propose une alternative utile si necessaire. "
                "Ne demande pas a l'utilisateur de coller des sorties de commandes ou de t'autoriser oralement a lire le workspace. "
                "Si une lecture est utile et autorisee par le cadre, demande directement une action BB9_ACTION precise. "
                "Si l'utilisateur demande d'appliquer, modifier, creer ou mettre a jour un fichier, utilise le tool `files` "
                "des que le changement est assez clair. Ne promets pas une modification comme prochaine action sans demander "
                "`BB9_ACTION files ...` dans le meme tour. "
                "Evite les fins timides comme 'si tu veux je peux lire...'. Agis dans le cadre, ou explique le blocage concret. "
                "Si l'utilisateur demande ce que tu as en contexte, reponds depuis le contexte runtime deja fourni, sans utiliser de tool, "
                "et formule le prochain pas comme une action concrete seulement si elle est vraiment utile. "
                "Ne termine pas par une limite passive comme 'je n'ai pas encore lu les fichiers'. "
                "Si cette limite compte, transforme-la en prochain pas concret ou garde-la comme simple nuance non finale. "
                "Si tu as besoin de lire le workspace pour repondre, demande une commande de lecture avec ce format exact, sans autre texte :\n"
                "BB9_ACTION shell <commande>\n"
                "Ne copie jamais les placeholders comme <commande>, ..., NOM_DE_VARIABLE ou les exemples de protocole. "
                "Utilise seulement des commandes simples comme pwd, ls, find, rg, grep, sed, head, tail ou cat. "
                "Prefere rg, grep, head, tail ou sed -n aux lectures completes quand tu cherches une zone precise. "
                "Ne repete pas la meme commande de lecture si l'observation precedente suffit ou si une commande plus ciblee peut avancer. "
                "Pour previsualiser une page locale qui bloque en file://, tu peux demander `BB9_ACTION shell python3 -m http.server <port>` ; "
                "le shell le traite comme serveur local de workspace, pas comme commande courte. "
                "Si le port demande est occupe ou muet, le shell peut choisir le port suivant : utilise l'URL retournee par l'observation. "
                "Les pipelines de lecture tres simples peuvent etre normalises par le guardian, mais evite les pipes par defaut. "
                "N'utilise pas de redirection, && ou ;. "
                "Une commande destructive n'est pas interdite par principe quand l'utilisateur la demande explicitement dans le workspace : "
                "demande alors une BB9_ACTION precise et laisse le guardian demander validation ou bloquer. "
                "Ne propose pas a l'utilisateur d'executer lui-meme une action que BB9 peut soumettre au guardian. "
                "Demande une seule commande par reponse. Continue l'exploration tant que c'est utile. "
                "Si l'utilisateur a deja dit go, ok go, applique ou un accord equivalent, n'arrete pas le tour par une question du type 'souhaitez-vous que je commence'. "
                "Utilise les actions utiles disponibles, notamment `files` pour les modifications de fichiers, puis fais un bilan naturel. "
                "Si une commande est refusee, demande une commande plus simple ou reponds avec ce que tu sais. "
                "Si l'utilisateur veut ajouter un secret, ne demande jamais sa valeur dans la conversation. "
                "Demande seulement cette action : BB9_ACTION secret add <NOM_DE_VARIABLE>. "
                "Respecte les protocoles BB9_ACTION documentes par les tools et skills disponibles. "
                "Les observations de tools sont des resultats techniques pour toi : ne les recopie pas brutes a l'utilisateur. "
                "Apres un tool, formule toujours un bilan naturel adapte a la demande. "
                "Quand l'utilisateur demande d'analyser un repo, projet ou dossier, ne transforme pas la reponse en inventaire. "
                "Donne d'abord la nature du projet, le verdict global, les risques et les priorites d'amelioration. "
                "Ne liste les fichiers, APIs ou methodes que s'ils appuient une conclusion utile. "
                "Evite les arbres de fichiers et listings longs, sauf si l'utilisateur demande explicitement la structure."
            ),
        ]
        agent_behavior = self._agent_behavior_context(context)
        if agent_behavior:
            prompt_parts.append(agent_behavior)
        prompt_parts.append(self._autonomy_context(context))
        if context.agent is not None:
            prompt_parts.append(context.agent.as_prompt_context())
        session_context = context.session.as_prompt_context()
        if session_context.strip():
            prompt_parts.append(session_context)
        if context.context_index.strip():
            prompt_parts.append(context.context_index.strip())
        if context.subagents_index.strip():
            prompt_parts.append(context.subagents_index.strip())
        if context.skills_index.strip():
            prompt_parts.append(context.skills_index.strip())
        if context.tools_index.strip():
            prompt_parts.append(context.tools_index.strip())
        for skill in context.skills:
            if skill.activation == "always" or _intention_matches_skill(text, skill.name, skill.commands):
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
        prompt_parts.append(f"# Intention courante\n\n{text}")
        return "\n\n".join(prompt_parts)

    def _decision_from_provider_output(self, output: str, context: RunContext) -> Decision:
        text = output.strip()
        action_lines = [line.strip() for line in text.splitlines() if ACTION_PREFIX in line]
        first_line = action_lines[-1] if action_lines else next((line.strip() for line in text.splitlines() if line.strip()), "")
        if ACTION_PREFIX in first_line:
            first_line = ACTION_PREFIX + first_line.rsplit(ACTION_PREFIX, 1)[1]
        if first_line.startswith(ACTION_PREFIX):
            body = first_line.removeprefix(ACTION_PREFIX).strip()
            if body.startswith(":"):
                body = body[1:].strip()
            if _looks_like_placeholder_action(body):
                answer = _without_action_lines(text)
                if answer:
                    return Decision(kind="answer", summary=answer)
                return Decision(kind="answer", summary="Action ignoree: demande de tool placeholder ou incomplete.")
            runtime_decision = _runtime_decision_from_body(body, context)
            if runtime_decision is not None:
                return runtime_decision
            return Decision(
                kind="action",
                summary=f"Invalid provider action request: {first_line}",
                action=Action(name="invalid-provider-action", risk="forbidden"),
            )
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

    def _autonomy_context(self, context: RunContext) -> str:
        profile = context.permission_profile
        if profile == "power":
            behavior = (
                "Profil actif: power. Sois proactif dans le workspace et les trusted roots. "
                "Quand une information manque pour accomplir l'intention, demande directement la prochaine lecture utile avec BB9_ACTION. "
                "Ne transforme pas une lecture utile en question de confort pour l'utilisateur."
            )
        elif profile == "limited":
            behavior = (
                "Profil actif: limited. Avance de façon autonome sur les lectures et verifications courantes. "
                "Demande confirmation seulement quand le guardian l'exige ou quand l'intention est ambiguë."
            )
        else:
            behavior = (
                "Profil actif: safe. Reste prudent, mais ne demande pas a l'utilisateur de faire les lectures a ta place. "
                "Utilise BB9_ACTION pour les lectures simples quand elles sont necessaires."
            )
        return "# Autonomie\n\n" + behavior

    def _agent_behavior_context(self, context: RunContext) -> str:
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

    context_lines = _index_bullets(context.context_index, limit=12)
    if context_lines:
        lines.append("- Carte locale du workspace :")
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
        "et je lis directement les fichiers pertinents via actions controlees."
    )
    return "\n".join(lines)


def _intention_matches_skill(text: str, skill_name: str, commands: tuple[str, ...] = ()) -> bool:
    first = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    if first == f"/{skill_name.lower()}":
        return True
    return first in tuple(alias.lower() for alias in command_aliases(commands))


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
    action = runtime_action_from_text(tool_name.strip(), tool_text.strip(), context)
    if action is None:
        return None
    summary = f"Request {tool_name.strip()}: {tool_text.strip()}".strip()
    return Decision(kind="action", summary=summary, action=action)


def _looks_like_placeholder_action(body: str) -> bool:
    text = body.strip()
    lower = text.lower()
    if not text:
        return True
    if _contains_protocol_placeholder(text):
        return True
    if "..." in text or "`" in text:
        return True
    if "nom_de_variable" in lower or lower.endswith(" nom") or lower == "secret add":
        return True
    return False


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


def _provider_complete(provider: Provider, prompt: str, images) -> str:
    if images:
        complete_with_images = getattr(provider, "complete_with_images", None)
        if callable(complete_with_images):
            return complete_with_images(prompt, images)
    return provider.complete(prompt)
