# Audit Parite Marius

## Intention

Comparer BB9 a Marius sans copier son architecture. BB9 ne doit pas faire moins
fonctionnellement ; il doit deplacer la complexite durable vers des archives
Markdown, garder un kernel petit, et laisser les interfaces remplaçables.

Lecture courte :

- Marius est deja un produit local complet : desktop, dashboard, gateway
  persistant, Telegram, Task Board, routines, storage specialise, tools nombreux.
- BB9 a deja un noyau plus sobre : archives Markdown, agents/skills/tools,
  cron, dream, sessions, memoire SQL graph, provider config, guardian, gateway
  local prudent, delegation minimale, `/plan` et `/build`.
- Le risque principal de BB9 n'est plus "manquer de noyau", mais manquer de
  certaines surfaces runtime que Marius a rendues utiles : historique visible,
  notifications, task board metier, adapters de canaux, registry tools plus
  riche, et persistance de rapports.

## 1. Kernel Et Loop

### Ce que Marius fait

Marius separe clairement les contrats (`Message`, `ToolCall`, `ToolResult`,
`Artifact`, `PermissionDecision`), l'orchestrateur de tour, la session runtime,
le router tools, le provider adapter, la compaction et le contexte projet.

Sa loop est plus riche :

- streaming possible ;
- retry provider avant premiere sortie visible ;
- appels tools iteratifs ;
- rendu de resultats outils dans le contexte ;
- artefacts structures (`diff`, `image`, `report`, `file`) ;
- compaction pre-tour selon pression de contexte ;
- separation contexte interne / historique visible.

### Ce que BB9 fait deja

BB9 a une loop synchrone lisible :

- `Intention -> Decision -> Action -> Observation -> Trace` ;
- budget d'outils par profil ;
- guardian + hooks + gateway ;
- provider OpenAI-compatible minimal ;
- session courte avec compaction ;
- contexte agent/skills/tools/subagents injecte ;
- outils appeles via protocole `BB9_ACTION`.

### Ecart utile

BB9 n'a pas encore de contrat d'artefacts. C'est un vrai manque si on veut
retrouver les diffs, captures, rapports, fichiers generes, screenshots ou
preuves dans les sessions, le dreaming et les futurs canaux.

BB9 n'a pas non plus la distinction forte Marius entre :

- contexte interne compactable ;
- historique visible utilisateur ;
- artefacts persistants ;
- observations outils utiles au tour suivant.

### Decision BB9 manquante

Definir un contrat minimal d'artefact BB9, probablement en Markdown-first :

```text
Artifact
- id
- kind: diff | image | report | file | screenshot | note
- title
- path
- source
- created_at
- metadata
```

Le code stocke l'artefact et ses metadonnees ; le Markdown porte le sens, la
politique de conservation et les regles de rendu.

## 2. Gateway, Daemon Et Mode Continu

### Ce que Marius fait

Marius a un gateway persistant par agent :

- socket Unix ;
- session durable ;
- streaming live vers clients ;
- permissions bloquantes avec reponse client ;
- scheduler integre ;
- web, Telegram et desktop connectes au meme runtime ;
- historique visible canonique synchronise ;
- session registry ;
- notifications de sessions.

### Ce que BB9 fait deja

BB9 a un gateway local prudent, mais pas un processus persistant central. Le
projet a volontairement differe le daemon.

Le cron BB9 sait calculer `due/next_run`, garder un etat runtime et declencher
une intention ou une commande explicite, mais sans worker de fond permanent.

### Ecart utile

Marius resout un probleme que BB9 devra reprendre : comment une action longue,
une routine, un rappel, un dream ou une session de travail continue existe hors
du REPL courant.

Mais reprendre le gateway Marius tel quel irait contre l'esprit BB9 si on en
fait le coeur du produit.

### Decision BB9 manquante

Le bon contrat semble :

- kernel sans daemon ;
- `bb9 tick` explicite pour cron/dream/routines ;
- mode continu lance explicitement (`bb9 run`, `bb9 watch`, ou equivalent) ;
- gateway persistant optionnel comme host, pas comme kernel ;
- etat runtime dans `~/.bb9/*.json` ou SQLite selon le type.

Le gateway BB9 doit etre une app externe branchable, pas une dependance du noyau.

## 3. Sessions Et Historique Visible

### Ce que Marius fait

Marius distingue :

- session runtime ;
- corpus de sessions pour dreaming ;
- registre de sessions ;
- historique web visible ;
- notifications vers canonique ;
- archives lisibles apres `/new`.

Cette distinction est centrale pour desktop/web/Telegram.

### Ce que BB9 fait deja

BB9 persiste des sessions dans SQLite et les rend consolidables par le dreaming.
Il a une session courte injectee dans le contexte provider et une compaction
manuelle/automatique.

### Ecart utile

BB9 n'a pas encore d'historique visible canonique distinct du contexte court.
Aujourd'hui la session sert surtout au contexte et au dream, pas a reconstruire
un fil visible multi-surface.

### Decision BB9 manquante

Ajouter une brique `visible_history` minimale, mais pas dashboard-first :

- append-only ;
- par agent et/ou workspace ;
- messages visibles seulement ;
- references vers artefacts ;
- evenements de notification structurables ;
- export Markdown lisible.

Elle ne doit pas remplacer `sessions.db`. Elle sert a l'utilisateur et aux
surfaces, pas au contexte interne brut.

## 4. Tools Et Gateway D'Actions

### Ce que Marius fait

Marius expose beaucoup de tools : filesystem, shell, explore, web, browser,
vision, memory, reminders, tasks, projects, RAG, CalDAV, sentinelle, host admin,
providers admin, approvals/secrets, self-update, spawn_agent, call_agent,
CodeGraph, allow_roots.

Les tools sont des `ToolEntry` avec schema, handler et resultats structures.

### Ce que BB9 fait deja

BB9 a des tools Markdown natifs :

- `shell` ;
- `web` ;
- `browser` ;
- `ui_web` ;
- `secret` ;
- `caldav` ;
- `create_skill` ;
- `project-explorer` ;
- `project-onboarding`.

Chaque tool peut avoir `TOOL.md`, `runtime.py`, `cli.py`, `DREAM.md`.
Le loader generic garde la logique native proche de l'archive.

### Ecart utile

BB9 a la bonne architecture, mais la surface fonctionnelle tools reste plus
etroite que Marius :

- pas de filesystem natif complet (`read/write/list/move/mkdir`) separe du shell ;
- pas de RAG Markdown ;
- pas de reminders ;
- pas de task board metier ;
- pas de provider admin par tool ;
- pas de approvals admin ;
- pas de self-update proposal flow ;
- pas de vision ;
- pas de CodeGraph/read-only ;
- pas de host diagnostics complet.

### Decision BB9 manquante

Eviter de grossir le kernel. Creer des tools natifs par archive quand la capacite
est commune et stable. Priorite probable :

1. `filesystem` ou enrichir `project-explorer` avec lecture/ecriture structurees ;
2. `tasks` pour persistence metier minimale ;
3. `reminders` ou le fusionner avec cron selon contrat ;
4. `rag` Markdown ;
5. `provider_admin` et `security_admin` ;
6. `artifacts` ;
7. `self_update`.

## 5. Skills Et Commandes

### Ce que Marius fait

Marius charge des skills globaux et projet. Les commandes vivent dans
`core/<command>.md` et sont declarees par frontmatter. Le skill `dev` est riche :
`/plan`, `/build`, `/test`, `/review`, `/commit`, `/resume`, `/pr`, `/projects`,
`/project`, `/tasks`, `/decision`, `/check`.

Il a aussi des skills metiers : assistant, autopilot, kanban, rag, sentinelle,
caldav_calendar, browser, onboarding, skill-creator.

### Ce que BB9 fait deja

BB9 a une definition plus claire :

- tools natifs dans le repo ;
- skills utilisateur dans `~/.bb9/skills`;
- skills locaux dans `.bb9/skills`;
- local prend le dessus sur global ;
- commandes declarees dans Markdown ;
- `cli.py` optionnel ;
- `runtime.py` optionnel ;
- `core.py` backend optionnel ;
- convention `/<skill>` et `/<skill>-<commande>`.

`/plan` et `/build` existent comme templates de skills utilisateur.

### Ecart utile

BB9 n'a pas encore les commandes dev de Marius autour de `/test`, `/review`,
`/commit`, `/resume`, `/pr`, `/decision`, `/check`. Ces commandes ne sont pas
du luxe : elles forment une grammaire de travail.

### Decision BB9 manquante

Deux options :

- garder un seul skill `dev` avec plusieurs commandes namespacées ;
- ou decouper en skills methodes (`plan`, `dev`, `review`, `commit`, `project`).

Dans l'esprit BB9, le meilleur choix est probablement :

- `plan` et `dev` restent des skills methodes majeurs ;
- `review`, `commit`, `test`, `project` peuvent etre des skills separes si leur
  Markdown devient substantiel ;
- les commandes creees par skill-create doivent privilegier le namespacing.

## 6. Subagents Et Multi-Agents

### Ce que Marius fait

Marius a `spawn_agent` :

- jusqu'a 5 workers par appel ;
- timeout ;
- limite iterations ;
- contexte fichiers borne ;
- outils filtres pour workers ;
- read-only possible ;
- workers sans memoire durable ;
- permissions non interactives ;
- rapports structures ;
- traces worker persistées.

Il a aussi la notion de session canonique et sessions de travail, mais elle
reste encore partiellement en chantier.

### Ce que BB9 fait deja

BB9 a maintenant :

- subagents Markdown locaux a l'agent ;
- heritage parent ;
- index subagents ;
- `Task` / `TaskResult` ;
- `delegate(task, subagent, parent_context, runner)` ;
- contexte reduit ;
- session `delegation:<task-id>` ;
- suppression de l'index subagents pour eviter recursion libre ;
- permission profile plafonne ;
- `/build` qui execute `.bb9/plan.md` avec dependances et parallelisme par `paths`.

### Ecart utile

BB9 n'a pas encore :

- timeout worker ;
- limites par task vraiment appliquees ;
- toolset filtrable par subagent ;
- traces worker persistées ;
- statut `timeout` ou `blocked` distinct de `error` ;
- demande d'arbitrage parent ;
- annulation ;
- worker long asynchrone.

### Decision BB9 manquante

Etendre `TaskResult.status` ou garder court ?

Option sobre :

```text
status: done | error
blockers: timeout | permission | dependency | needs_arbitration
```

Cela garde le contrat court et evite de multiplier les statuts. Mais il faut
quand meme ajouter timeout, max_iterations, tool allowlist et trace worker.

## 7. Cron, Routines, Rappels Et Scheduler

### Ce que Marius fait

Marius unifie Task Board et routines dans `TaskStore` :

- task unique ;
- task planifiee via `scheduled_for` ;
- routine via `recurring + cadence` ;
- retries ;
- locks ;
- attempts ;
- events ;
- scheduler dans gateway ;
- routines jamais `done`.

Marius gere aussi des reminders dedies, surtout pour Telegram.

### Ce que BB9 fait deja

BB9 a un contrat `CRON.md` plus elegant :

- `Mode: once | recurring` ;
- `At` pour unitaire ;
- `Time` et `Days` pour recurrent ;
- timezone ;
- retry ;
- notification ;
- history ;
- state runtime ;
- calcul pur `due/next_run` ;
- execution explicite par `/cron tick` et support `/dream run`.

BB9 est deja plus clair que Marius sur les jours de semaine.

### Ecart utile

BB9 n'a pas encore :

- scheduler de fond ;
- locks robustes multi-process ;
- notification adapters ;
- historique riche d'evenements ;
- lien avec une persistence metier de tasks ;
- reminders conversationnels simples.

### Decision BB9 manquante

Ne pas remplacer `CRON.md` par un TaskStore. Garder :

- cron = declencheur ;
- tasks = travail metier ;
- reminders = cas special de notification utilisateur ou archive cron simplifiee.

Un cron peut lancer une command, une intention, un dream ou une task. Mais il ne
doit pas devenir le store metier principal.

## 8. Dreaming Et Memoire

### Ce que Marius fait

Marius dream :

- collecte memoire SQLite+FTS ;
- sessions non traitees ;
- DREAM.md des skills ;
- DECISIONS/ROADMAP ;
- self-update signals ;
- appel LLM ;
- operations JSON ;
- actions skill proposees ;
- rapport JSON persiste ;
- archive sessions traitees.

### Ce que BB9 fait deja

BB9 a deja une memoire SQL graph plus ambitieuse que Marius :

- nodes ;
- edges ;
- scope global/project ;
- kind/tags/source/confidence ;
- FTS si disponible.

Le dreaming BB9 collecte :

- memoire ;
- edges ;
- sessions ;
- contributions `DREAM.md` des skills/tools ;
- DECISIONS/ROADMAP du projet ;
- operations node/edge ;
- actions proposees ;
- preview/apply via pending plan ;
- cron peut lancer `/dream run`.

### Ecart utile

BB9 n'a pas encore :

- rapports dream persistés ;
- archivage/marking des sessions traitees ;
- self-update signals ;
- outil `dreaming_run` exposable au modele ;
- politique de retention dream.

### Decision BB9 manquante

Ajouter `~/.bb9/dreams/reports/<timestamp>.json` ou `.md + .json`.

Le rapport ne doit pas polluer la memoire durable. Il sert a l'audit, au debug
et a l'historique des consolidations.

## 9. Persistence Metier

### Ce que Marius fait

Marius a beaucoup de stores :

- tasks ;
- reminders ;
- approvals ;
- allowed roots ;
- providers ;
- projects ;
- goals ;
- logs ;
- memory ;
- RAG ;
- session registry ;
- session corpus ;
- UI history ;
- worker runs ;
- secret refs.

### Ce que BB9 fait deja

BB9 a :

- memory.db ;
- sessions.db ;
- cron-state.json ;
- dream-pending.json ;
- provider config ;
- trusted roots ;
- secrets ;
- logs minimaux ;
- goal state ;
- historique visible et artefacts ;
- rapports de dream ;
- tasks metier minimales dans `~/.bb9/tasks/tasks.json`.

### Ecart utile

La plus grosse absence BB9 cote produit est maintenant moins large : la première
persistance métier `tasks` existe, mais il manque encore reminders, projects,
approvals, worker reports et les branchements automatiques autour de ces stores.

BB9 commence donc à pouvoir "tenir" du travail dans le temps, mais il ne dispose
pas encore du task board, des notifications, des locks et des workers qui rendent
Marius plus complet côté produit.

### Decision BB9 manquante

Faire un store generique Markdown-first ou plusieurs stores specialises ?

Proposition :

- stores runtime simples en JSON/SQLite ;
- contrats Markdown pour expliquer le sens ;
- pas de config metier dans Python.

Premiere tranche implémentée :

```text
~/.bb9/tasks/tasks.json
bb9/tools/tasks/TOOL.md
docs/tasks.md
```

Le Markdown porte les règles ; `tasks.json` porte l'état.

## 10. Provider, Config Et Secrets

### Ce que Marius fait

Marius supporte :

- OpenAI compatible ;
- ChatGPT OAuth ;
- Ollama ;
- config provider editable ;
- provider admin tools ;
- secret refs ;
- auth flow ;
- doctor.

### Ce que BB9 fait deja

BB9 a repris une config provider minimale, un registry, un choix de modele, un
adapter OpenAI-compatible, un auth flow ChatGPT-web minimal, et un tool `secret`.

### Ecart utile

BB9 n'a pas encore toute la surface admin :

- provider list/save/delete/models comme tools ;
- doctor complet ;
- validation de config ;
- Ollama/Anthropic si souhaité ;
- gestion approvals/secrets administrable depuis agent.

### Decision BB9 manquante

La config sensible doit rester hors Markdown. Mais le contrat provider peut etre
decrit en Markdown :

```text
PROVIDERS.md = politique, providers acceptes, champs non sensibles
providers.json = etat local non versionne
secrets = references seulement
```

## 11. RAG, Web, Browser, Vision, CodeGraph

### Ce que Marius fait

Marius a :

- `web_fetch`, `web_extract`, `web_search` via SearxNG ;
- browser Playwright ;
- vision ;
- RAG Markdown ;
- CodeGraph read-only.

### Ce que BB9 fait deja

BB9 a deja `web`, `browser`, `ui_web` et `project-explorer`.

### Ecart utile

Le trou important est RAG Markdown, car il colle tres bien a BB9. Vision et
CodeGraph sont utiles mais moins centraux pour l'architecture.

### Decision BB9 manquante

Prioriser `rag` comme archive native :

```text
bb9/tools/rag/TOOL.md
bb9/tools/rag/runtime.py
bb9/tools/rag/DREAM.md
```

Le RAG BB9 devrait indexer des sources Markdown et produire des references
lisibles, pas devenir un moteur opaque.

## 12. Dashboard, Channels Et Render

### Ce que Marius fait

Marius a :

- dashboard complet ;
- app desktop Electron ;
- web direct ;
- Telegram ;
- render Markdown cross-canaux ;
- i18n dashboard ;
- historique visible et SSE.

### Ce que BB9 fait deja

BB9 n'en fait pas son coeur. C'est volontaire et aligne avec le projet.

### Ecart utile

Il manque quand meme un contrat de render et d'historique visible si BB9 veut
brancher plus tard une UI externe sans toucher au kernel.

### Decision BB9 manquante

Ecrire un contrat `channels/render` minimal :

- entree logique ;
- sortie Markdown portable ;
- artefacts references ;
- notifications ;
- aucune dependance dashboard.

Le dashboard eventuel doit consommer ces contrats, pas les inventer.

## Priorisation Proposee

### Court terme

1. **Artefacts + historique visible minimal**  
   C'est la base pour reprendre proprement les traces, rapports, screenshots,
   diffs, notifications et futurs channels.
   Première tranche BB9 : `Artifact`, `VisibleMessage`,
   `~/.bb9/visible-history.db` et `/history`.

2. **Rapports de dream persistés**  
   Petit chantier, tres aligne avec le dreaming deja en place.
   Premiere tranche BB9 : rapports JSON+Markdown dans
   `~/.bb9/dreams/reports/`, attaches comme artefacts `report`.

3. **Persistence metier minimale `tasks`**  
   Reprendre l'idee Marius TaskStore, mais avec contrat Markdown BB9. Ne pas
   confondre avec cron.

4. **Durcir delegation/subagents**  
   Timeout, max_iterations, tool allowlist, worker reports, blockers typés.

### Moyen terme

5. **RAG Markdown natif**  
   Tres compatible avec l'ADN BB9.

6. **Provider/security admin tools**  
   Utile pour piloter BB9 depuis l'agent sans dashboard.

7. **Mode continu explicite**  
   Un host optionnel qui tick cron/dream/tasks, sans daemon obligatoire.

8. **Skills dev complementaires**  
   `/review`, `/test`, `/commit`, `/resume`, `/decision`, `/check`, soit dans
   `dev`, soit comme skills methodes separes.

### Plus tard

9. **Notifications adapters**  
   Local d'abord, Telegram/web ensuite si besoin.

10. **Dashboard/app externe**  
    Un consommateur des contrats, jamais le centre du kernel.

## Critique Centrale

BB9 est maintenant sain architecturalement, mais il doit faire attention a un
angle mort : a force de tout rendre elegant et Markdown-first, il peut manquer
les petits stores runtime qui donnent a Marius son utilite continue.

La bonne formule n'est pas "plus de Python". C'est :

- Markdown pour le contrat, la politique, l'intention et la decouverte ;
- stores runtime sobres pour l'etat vivant ;
- kernel petit pour executer les contrats ;
- hosts optionnels pour cron, notifications, dashboard ou canaux.

En pratique, les prochains chantiers doivent surtout combler la difference entre
"BB9 sait faire un tour agentique" et "BB9 sait tenir un travail dans le temps".
