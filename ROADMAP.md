# Roadmap

## Phase 0 — Structuration

- [x] Créer les fichiers de gouvernance du projet.
- [x] Créer les premiers contrats Markdown dans `docs/`.
- [x] Clarifier le rôle de `AGENTS.md`.
- [x] Capturer les signaux externes utiles sans ajouter de dépendance.
- [x] Prendre en compte les subagents dans les contrats de conception.
- [x] Positionner le guardian et les hooks entre modèle et tools.
- [x] Séparer memory, trace, session, context-index et workspace dans les contrats.
- [x] Supprimer `DOC.md` pour éviter le doublon avec `docs/`.
- [x] Clarifier le kernel comme point d'entrée logique léger.
- [x] Poser la découverte des agents Markdown.
- [x] Déplacer la source active des agents vers le dossier user.
- [x] Poser les skills Markdown utilisateur et leur désactivation par agent.
- [x] Poser les tools Markdown globaux et leur désactivation par agent.
- [x] Poser les subagents Markdown locaux avec héritage depuis l'agent parent.
- [x] Clarifier `shell` et l'exploration projet comme tools natifs autonomes.
- [x] Retirer `markdown-first` des skills pour le garder comme principe structurel.
- [x] Poser les profils de permission `safe`, `limited`, `power`.
- [x] Poser les trusted roots persistants.
- [x] Clarifier le vocabulaire repo, dossier user, workspace et trusted root.
- [x] Ranger le runtime dans `bb9/core/` et les tools natifs dans `bb9/tools/`.
- [ ] Relire et stabiliser les contrats des briques système.
- [ ] Identifier les questions bloquantes avant code.

## Phase 1 — Noyau minimal

- [x] Définir la forme minimale d'une intention.
- [x] Définir la forme minimale d'une action.
- [x] Définir la forme minimale d'une observation.
- [x] Définir la forme minimale d'une trace.
- [x] Définir les logs runtime minimaux.
- [x] Définir la forme minimale d'une décision du guardian.
- [x] Définir le workspace minimal d'un run local.
- [x] Brancher les trusted roots dans les décisions de tools fichiers/shell.
- [x] Définir la relation minimale entre kernel, memory et context-index.
- [x] Injecter l'historique court de session dans le contexte provider.
- [x] Ajouter une compaction manuelle et automatique du contexte court de session.
- [x] Charger un agent Markdown dans le contexte du kernel.
- [x] Charger les skills Markdown utilisateur actifs dans le contexte du kernel.
- [x] Charger les tools Markdown actifs dans le contexte du kernel.
- [x] Générer des index Markdown pour skills et tools.
- [x] Protéger la mémoire `.bb9/` de workspace contre un commit accidentel.
- [x] Implémenter une première loop synchrone.
- [x] Implémenter un gateway local prudent.
- [x] Permettre au provider de demander un tool via un protocole minimal contrôlé.
- [x] Remplacer la limite basse de tools par un budget profilé plus proche de Codex.
- [x] Ajouter un provider OpenAI-compatible minimal sans dépendance externe.
- [x] Reprendre de Marius une config provider minimale avec registre et choix de modèle.
- [x] Supprimer le tool provisoire `echo` quand un vrai premier tool existe.
- [x] Implémenter une première exécution prudente du tool `shell`.
- [ ] Préserver une interface de délégation simple pour futurs subagents.

## Phase 2 — Interfaces

- [x] Ajouter une entrée CLI minimale.
- [x] Ajouter un mode CLI interactif.
- [x] Permettre de changer le profil de permission dans le REPL.
- [x] Rendre `python3 -m bb9` lançable depuis un autre workspace.
- [x] Ajouter un installateur utilisateur pour créer la commande `bb9` et migrer la config.
- [x] Ajouter une config locale non sensible pour le provider actif.
- [x] Ajouter une stratégie de secrets élégante.
- [x] Porter un adapter runtime ChatGPT-web minimal depuis Marius.
- [x] Ajouter une première boucle `/goal` persistante et bornée.
- [ ] Tracer les sessions sans bruit excessif.
- [ ] Configurer le niveau de logs localement.
- [ ] Permettre un mode continu lancé explicitement par l'utilisateur.
- [ ] Différer le daemon au démarrage tant que le mode continu n'est pas fiable.

## Stabilisation courte

- [x] Clarifier que les trusted roots vivent dans le dossier user.
- [x] Documenter que les extensions CLI de skills sont du code local de confiance.
- [x] Ajouter des tests ciblés sur les frontières : workspace `.bb9`, trusted roots, chargeur de tools.
- [ ] Décider si les skills auront un `runtime.py` autonome ou seulement des extensions REPL.
- [ ] Extraire `provider_config.py` seulement quand les tests rendent le découpage peu risqué.
- [ ] Extraire `bb9/core/cli.py` seulement par morceaux stables et sans changer l'UX.
