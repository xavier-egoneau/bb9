# Agentic System Minimal

Un système agentique élégant, minimal et compréhensible.

## Idée

Construire un noyau agentique lisible, piloté par des contrats Markdown et implémenté progressivement en Python seulement quand l'exécution réelle l'exige.

Le projet ne cherche pas à produire un framework agentique généraliste. Il cherche à définir un système assez simple pour être compris, audité, modifié et étendu sans perdre le contrôle.

BB9 n'est pas moins ambitieux fonctionnellement qu'un assistant local complet. Il est plus strict sur l'emplacement de la complexité : le kernel exécute des contrats courts ; le Markdown porte l'intention, la configuration, les comportements, les politiques et les workflows ; les interfaces restent remplaçables.

Le minimalisme de BB9 est donc un minimalisme de structure et de compréhension
humaine, pas un objectif de petitesse fonctionnelle. Le projet peut servir de
harness agentique général, d'assistant local complet ou de runtime lancé sur une
archive spécialisée, tant que l'utilisateur peut encore comprendre où vivent les
intentions, les règles, les outils, les permissions et les états.

L'esprit est proche de Pi Coding Agent (`https://pi.dev/`) : un harness minimal
que l'utilisateur adapte à ses workflows plutôt qu'un produit qui impose une
manière de travailler. La différence structurante de BB9 est le parti pris
Markdown-first : les variations durables doivent rester inspectables,
copiables et modifiables sous forme d'archives Markdown ; Python fournit les
primitives d'exécution, de validation, de trace et d'interface.

## Lignes directrices

- Markdown pour penser, cadrer, décider, documenter et garder la mémoire projet.
- Python pour charger, valider, exécuter, vérifier, appeler des providers, parser, tracer et exposer des runners génériques.
- Le coeur fournit des primitives réutilisables ; les workflows spécialisés doivent rester dans des archives Markdown quand c'est possible.
- Les concepts sont nommés tôt, mais implémentés seulement quand leur utilité est claire.
- Le système doit rester lisible avant d'être puissant.
- Le kernel décide, la loop orchestre, le gateway exécute, le guardian autorise ou bloque.
- La session porte le contexte court ; le gateway peut y rattacher des observations mais ne la possède pas.
- `AGENTS.md` décrit les consignes pour les agents contributeurs, pas les agents internes du produit.
- Aucun framework agentique lourd n'est ajouté sans décision explicite.
- Les agents, skills, tools, cron, dreams et workflows doivent d'abord être décrits comme archives Markdown découvrables.
- Une interface comme un dashboard peut exister plus tard, mais seulement comme client externe du runtime ou du gateway, pas comme coeur du système.

## Structure actuelle

- `README.md` : vision et usage du projet.
- `AGENTS.md` : consignes pour les agents contributeurs.
- `DECISIONS.md` : décisions durables.
- `ROADMAP.md` : phases de travail.
- `MEMORY.md` : faits projet durables.
- `docs/` : contrats par brique système.
- `docs/markdown-archives.md` : contrat commun des briques pilotées par Markdown.
- `bb9/tools/` : capacités natives BB9, chacune sous forme d'archive autonome Markdown avec backend optionnel.
- `bb9/templates/agents/` : templates d'agents installés dans le dossier user si absents.
- `bb9/templates/skills/` : templates de skills utilisateur installés si absents.
- `~/.bb9/agents/` : identités agents utilisateur en Markdown.
- `~/.bb9/skills/` : extensions utilisateur globales et partageables.
- `.bb9/skills/` : extensions locales au workspace, prioritaires sur les skills globaux du même nom.
- `~/.bb9/sessions.db` : sessions récentes persistées pour reprise, audit léger et dreaming.
- `~/.bb9/memory.db` : mémoire durable SQL graph.
- `bb9/core/` : runtime Python minimal.
- `bb9/__main__.py` et `bb9/cli.py` : points d'entrée compatibles.

Vocabulaire local :

- `repo` : ce dépôt BB9, où vit le code natif.
- `dossier user` : `~/.bb9/`, où vivent les choix privés et persistants de l'utilisateur.
- `workspace` : dossier dans lequel BB9 est lancé pour travailler sur un projet.
- `trusted root` : workspace ou dossier hors workspace déjà autorisé durablement par l'utilisateur.

## Lancement

Installation locale utilisateur, pour lancer BB9 depuis n'importe quel workspace :

```bash
cd PATH DU DOSSIER
python3.11 -m bb9.install
# Windows :
py -3.11 -m bb9.install
```

L'installateur demande Python 3.11+, expose le dépôt au Python utilisateur, crée la commande `bb9`, ajoute son dossier au `PATH` utilisateur quand c'est possible, crée `~/.bb9/` avec ses dossiers locaux, installe les agents et skills par défaut si absents, et migre l'ancienne config provider locale vers `~/.bb9/`.

Installation standard Python, utile dans une venv ou via pipx :

```bash
python3.11 -m pip install -e .
```

```bash
python3.11 -m bb9 "bonjour bb9"
```

Apres installation :

```bash
bb9 "bonjour bb9"
```

Mode interactif :

```bash
bb9
```

Chat web local :

```bash
bb9 web
```

Par défaut, le channel web écoute sur `http://127.0.0.1:8770`. Il réutilise le
même runtime que le CLI : session courte, provider configuré, tools, guardian,
trace, validation, images jointes et historique visible. `bb9 web` utilise le
provider configuré par défaut.
Pour choisir un autre port :

```bash
bb9 web --web-port 8780
```

Channel Telegram de l'agent actif :

`bb9 web` lance automatiquement le channel Telegram en fond si l'agent actif a
Telegram activé dans `TELEGRAM.md`. Si Telegram est activé depuis la modale
agent pendant que le web tourne, le host Telegram démarre sans autre commande.

La commande dédiée reste disponible pour diagnostiquer ou lancer Telegram sans
le web :

```bash
bb9 telegram
```

Le channel lit `~/.bb9/agents/<agent>/TELEGRAM.md`, résout le token via le store
de secrets local, filtre les `AllowedChatIds`, route les messages vers l'accueil
de l'agent, puis répond dans Telegram. Pour diagnostiquer sans laisser le poller
tourner :

```bash
bb9 telegram --telegram-once
```

Arrêter les hôtes BB9 locaux encore actifs :

```bash
bb9 stop
```

Cette commande arrête les processus BB9 locaux détectés (`web`, `telegram` ou
autres lancements `python -m bb9`) avec un arrêt doux puis forcé si nécessaire.

Quand le guardian demande validation, le REPL peut refuser, autoriser l'action une fois, ou ajouter un dossier hors workspace aux trusted roots du dossier user. Le chat web peut aussi mémoriser explicitement une action exacte dans `~/.bb9/approvals.json`.

Ou :

```bash
python3.11 -m bb9
python3.11 -m bb9.cli
```

Commandes interactives :

```text
/help
/context
/model
/goal
/dream
/profil
/compact
/secret
/secrets
/new
/exit
```

Choisir un profil de permission :

```bash
python3.11 -m bb9 --profile safe "bonjour"
python3.11 -m bb9 --profile limited "bonjour"
python3.11 -m bb9 --profile power "bonjour"
```

En mode interactif :

```text
/profil
/profil limited
/profil power
```

Le choix fait avec `/profil` est persistant dans `~/.bb9/settings.json`.
L'option `--profile` reste une surcharge pour le lancement courant.

Avec un provider OpenAI-compatible :

```bash
OPENAI_API_KEY=... python3.11 -m bb9 --provider openai-compatible --model gpt-4o-mini "bonjour"
```

Lister les providers connus :

```bash
bb9 --list-providers
```

Lister les modèles d'un provider :

```bash
OPENROUTER_API_KEY=... python3.11 -m bb9 --list-models openrouter
```

Configurer le provider actif en interactif :

```bash
python3.11 -m bb9
/model
```

`/model` permet de choisir une auth API key ou une auth web ChatGPT/Codex. Le chemin web ouvre un navigateur et attend le retour local sur `http://localhost:1455/auth/callback`.

Utiliser le provider actif configuré en one-shot :

```bash
bb9 --provider configured "bonjour"
```

La config provider utilisateur vit par defaut dans `~/.bb9/providers.json`.
Un chemin explicite peut la surcharger avec `BB9_PROVIDER_CONFIG_PATH` ou `--provider-config-path`.

Pour voir la trace d'un run :

```bash
python3.11 -m bb9 --show-trace "/action demo"
```

Lister les agents Markdown disponibles :

```bash
python3.11 -m bb9 --list-agents
```

Lister les subagents Markdown d'un agent :

```bash
python3.11 -m bb9 --agent default --list-subagents
```

Lister les skills utilisateur disponibles :

```bash
python3.11 -m bb9 --list-skills
```

Les skills Markdown peuvent aussi être appelés en REPL par leur nom slash si un
skill correspondant existe. Par exemple, les templates utilisateur `plan` et
`dev` rendent `/plan ...` et `/build ...` utilisables sans fichier Python dédié.
Un skill local dans `.bb9/skills/` prend le dessus sur un skill global du même
nom dans `~/.bb9/skills/`.
Les commandes propres aux skills et tools sont déclarées dans leur section
`## Commandes`; elles apparaissent dans les index et dans l'aide du REPL.

Lister les tools Markdown disponibles :

```bash
python3.11 -m bb9 --list-tools
```

Rafraichir les index Markdown des skills utilisateur et tools natifs :

```bash
python3.11 -m bb9 --refresh-indexes
```

Les index sont aussi régénérés automatiquement au lancement de `bb9`.

BB9 génère un index des subagents du pool plat injecté dans le contexte de l'agent parent. Les agents et subagents partagent le même répertoire `~/.bb9/agents/` ; un subagent se déclare via `Type : subagent` dans son `IDENTITY.md`.

Le subagent `worker` sert de fallback pour une délégation bornée quand aucune spécialisation ne correspond mieux.
`/goal` n'est pas un subagent : c'est une commande d'orchestration longue attachée à l'agent courant.
Ses itérations utilisent le worker `dev` s'il est configuré, sinon un worker `dev` éphémère issu du template générique.

BB9 génère aussi une carte courte dans le workspace courant :

```text
.bb9/context-index.md
```

Ce fichier fait partie de sa mémoire de travail locale et régénérable. Si BB9 est lancé dans un autre projet, ce fichier doit apparaître dans cet autre projet, pas dans le dépôt BB9.
BB9 crée aussi `.bb9/.gitignore` dans le workspace pour éviter de versionner cette mémoire de travail par accident.

Les secrets locaux peuvent être référencés sans exposer leur valeur :

```text
secret:OPENAI_API_KEY
```

Un agent peut demander la création d'un secret avec `BB9_ACTION secret add <NOM>`. En REPL, BB9 demande validation puis ouvre une capture locale : la prochaine saisie `secret>` est stockée sans passer par le provider.

Si un secret est collé par erreur hors procédure, le REPL tente aussi de l'intercepter avant l'appel provider et propose de le stocker localement.

BB9 inclut aussi un tool CalDAV local, si `vdirsyncer` et `khal` sont configurés :

```text
BB9_ACTION caldav doctor
BB9_ACTION caldav agenda days=7
```

Lire le web ou chercher des sources publiques :

```text
BB9_ACTION web fetch url=https://example.org
BB9_ACTION web search query="bb9 minimal agent"
```

Tester une page créée par l'agent avec Playwright :

```text
BB9_ACTION browser check url=http://127.0.0.1:3000 text="Accueil" selector=main screenshot=true
```

Si Playwright ou Chromium manque, le tool retourne une observation claire. Il
reste optionnel et n'ajoute pas de framework au runtime BB9.

Ouvrir l'helper web local pour coller ou déposer un screenshot :

```text
/web
```

Les images sont stockées dans `.bb9/uploads/web/` et la page retourne une
référence `[image: /chemin/image.png]` à coller dans la discussion.
BB9 résout uniquement les images sous `.bb9/uploads/` ou
`.bb9/artifacts/screenshots/` du workspace courant. Quand le provider le
supporte, ces images sont transmises comme entrées multimodales ; sinon elles
restent visibles comme références contrôlées dans le contexte.

Un tool ou un skill est une archive Markdown autonome, avec backend optionnel. Les tools vivent dans l'archive BB9 ; les skills globaux vivent dans `~/.bb9/skills/`, les skills locaux vivent dans `.bb9/skills/`, et ils peuvent être copiés d'un BB9 ou d'un projet à un autre.

Définir un objectif autonome :

```text
/goal Corrige tous les tests jusqu'à ce que npm test passe
/goal status
/goal pause
/goal resume
/goal cancel
/goal clear
```

Un goal est persistant dans `~/.bb9/goals/active.json`. BB9 boucle tant que l'objectif est actif, puis s'arrête sur succès vérifié, blocage, pause, annulation ou limite.

Inspecter ou lancer le dreaming :

```text
/dream status
/dream context
/dream prompt
/dream preview
/dream apply
/dream run
```

Les archives vivent dans `~/.bb9/dreams/<name>/DREAM.md`. Un run dreaming
appelle le provider actif, consolide les sessions, la mémoire et les
contributions skills/tools, puis applique seulement les opérations mémoire SQL
graph retournées.
Le chemin `preview` puis `apply` permet de valider optionnellement les
opérations avant écriture dans `~/.bb9/memory.db`.

Compacter le contexte court de la session REPL :

```text
/compact
```

BB9 compacte aussi automatiquement les anciens messages de session quand le contexte court devient trop long. Cette compaction reste interne à la session : elle ne modifie pas `MEMORY.md`.
La fenêtre de contexte du modèle actif est résolue depuis le cache local `~/.bb9/model-metadata.json`, une table connue embarquée, puis un fallback prudent. L'auto-compaction ne fait pas de requête web implicite.
Les sessions sont aussi persistées dans `~/.bb9/sessions.db` pour la reprise locale et le dreaming. Cette archive runtime ne remplace pas la mémoire durable : elle fournit seulement des conversations récentes que le moteur peut consolider explicitement.

Exécuter une commande de lecture via le tool `shell` :

```bash
python3.11 -m bb9 --shell "rg --files"
```

Vérifier le runtime minimal :

```bash
python3.11 -m ruff check .
python3.11 -m unittest discover -q
```

`mypy` est configuré pour diagnostic, mais n'est pas encore une gate qualité : la dette de typage doit être corrigée progressivement avant de le rendre bloquant.

## Contrats étudiés

### Socle

- kernel
- loop
- gateway
- guardian
- hooks
- session
- trace
- logs
- workspace
- memory
- context-index
- agents
- goals

### Interfaces et adaptation

- channels
- providers
- tools
- config
- secrets
- skills

### Évolutions cadrées

- cron
- subagents
- dreams
- archives Markdown

Les signaux de veille utiles à la conception sont suivis dans `docs/external-signals.md`.
