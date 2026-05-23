# Signaux externes

## Intention

Conserver les enseignements utiles observés dans les agents récents sans transformer le projet en copie d'un outil existant.

Ce document sert de veille de conception. Il aide à ne pas oublier les idées importantes, mais il ne crée pas de dépendance technique.

## Signaux utiles

- Les agents modernes renforcent les profils de permission, le sandboxing et les validations explicites.
- Les hooks avant et après action sont utiles pour inspecter, bloquer ou tracer une action sans mélanger orchestration et exécution.
- Les subagents deviennent un motif récurrent : contexte séparé, mission bornée, droits limités et résultat synthétique.
- La mémoire durable doit rester séparée de la session courte et des traces temporaires.
- Les routines planifiées et le mode continu sont utiles, mais doivent rester optionnels et contrôlés.
- MCP apparaît comme une frontière possible pour exposer des tools, sans devoir devenir le cœur du système.
- Les agents long-horizon restent fragiles : la boucle doit rester limitée, observable et interrompable.
- Les mémoires, skills, routines et cron peuvent devenir des canaux dormants d'instructions indésirables.
- Les agents personnels récents poussent la mémoire locale et éditable, mais l'ingestion automatique doit rester explicite.
- Les code graphs locaux réduisent la découverte brute par grep/read et favorisent des requêtes de contexte structurées.
- Les orchestrateurs multi-agents récents isolent les tâches dans des workspaces séparés et gardent l'auto-run configurable.

## Conséquences pour ce projet

- Définir tôt les formes minimales de `intention`, `action`, `observation`, `trace` et `guardian decision`.
- Prévoir les subagents dès la conception, mais ne pas imposer un système multi-agent avant que la boucle simple fonctionne.
- Garder le `guardian` comme passage obligé pour les actions sensibles, y compris en mode continu, cron ou subagent.
- Placer le pre-action hook et le guardian avant les tools pour bloquer une action avant effet de bord ; garder le post-action hook pour sécuriser l'observation.
- Laisser le choix entre exécution ponctuelle, mode continu lancé par l'utilisateur et daemon au démarrage.
- Séparer `memory`, `trace`, `session` et `context-index`.
- Prévoir un `workspace` comme frontière d'exécution locale avant de penser dashboard ou orchestration parallèle.
- Considérer les index de contexte comme des aides régénérables, pas comme mémoire durable ni source d'autorité.
- Ne pas ajouter MCP, dashboard, mémoire vectorielle ou marketplace avant un usage réel.

## Repères suivis

- OpenClaw : architecture locale, gateway, approvals et usage possible de MCP.
- Claude Code : permissions, hooks, subagents et séparation des contextes.
- Codex : sandboxing, profils de permission, outils et orchestration par tâches.
- Hermes Agent : mémoire, sessions, cron, daemon et gateway.
- OpenHuman : mémoire locale, wiki éditable, ingestion connecteurs et mode personnel continu.
- CodeGraph : graphes locaux de code, requêtes structurées, réduction des appels tools et tokens.
- Superset : workspaces isolés, agents parallèles, presets, auto-run configurable et automations.

## Sources initiales

- https://github.com/openclaw/openclaw
- https://docs.openclaw.ai/concepts/architecture
- https://developers.openai.com/codex/changelog
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/sub-agents
- https://hermes-agent.nousresearch.com/docs/user-guide/features/memory
- https://hermes-agent.nousresearch.com/docs/user-guide/features/cron
- https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp
- https://arxiv.org/abs/2605.13471
- https://arxiv.org/abs/2605.10912
- https://github.com/tinyhumansai/openhuman
- https://github.com/colbymchenry/codegraph
- https://sebastiantirelli.com/writing/codegraph/
- https://docs.superset.sh/agent-integration
- https://docs.superset.sh/setup-teardown-scripts
- https://docs.superset.sh/terminal-presets
- https://docs.superset.sh/automations
