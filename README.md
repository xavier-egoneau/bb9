# Agentic System Minimal

Un système agentique élégant, minimal et compréhensible.

## Idée

Construire un noyau agentique lisible, piloté par des contrats Markdown et implémenté progressivement en Python seulement quand l'exécution réelle l'exige.

Le projet ne cherche pas à produire un framework agentique généraliste. Il cherche à définir un système assez simple pour être compris, audité, modifié et étendu sans perdre le contrôle.

## Lignes directrices

- Markdown pour penser, cadrer, décider, documenter et garder la mémoire projet.
- Python pour agir, vérifier, appeler des providers, parser, tracer et exposer une interface minimale.
- Les concepts sont nommés tôt, mais implémentés seulement quand leur utilité est claire.
- Le système doit rester lisible avant d'être puissant.
- Le kernel décide, la loop orchestre, le gateway exécute, le guardian autorise ou bloque.
- La session porte le contexte court ; le gateway peut y rattacher des observations mais ne la possède pas.
- `AGENTS.md` décrit les consignes pour les agents contributeurs, pas les agents internes du produit.
- Aucun framework agentique lourd n'est ajouté sans décision explicite.
- Pas de multi-agent, base vectorielle, dashboard ou queue tant qu'une boucle simple ne fonctionne pas.

## Structure actuelle

- `README.md` : vision et usage du projet.
- `AGENTS.md` : consignes pour les agents contributeurs.
- `DECISIONS.md` : décisions durables.
- `ROADMAP.md` : phases de travail.
- `MEMORY.md` : faits projet durables.
- `docs/` : contrats par brique système.
- `bb9/tools/` : capacités natives BB9, chacune sous forme d'archive autonome Markdown avec backend optionnel.
- `bb9/templates/agents/` : templates d'agents installés dans le dossier user si absents.
- `~/.bb9/agents/` : identités agents utilisateur en Markdown.
- `~/.bb9/skills/` : extensions utilisateur partageables.
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
cd /home/egza/Documents/projets/agentic-system-minimal
python3 install.py
```

L'installateur expose le dépôt au Python utilisateur, crée la commande `bb9` dans `~/.local/bin/`, crée `~/.bb9/` avec ses dossiers locaux, installe les agents par défaut si absents, et migre l'ancienne config provider locale vers `~/.bb9/`.

```bash
python3 -m bb9 "bonjour bb9"
```

Apres installation :

```bash
bb9 "bonjour bb9"
```

Mode interactif :

```bash
bb9
```

Quand le guardian demande validation, le REPL peut refuser, autoriser l'action une fois, ou ajouter un dossier hors workspace aux trusted roots du dossier user.

Ou :

```bash
python3 -m bb9
python3 -m bb9.cli
```

Commandes interactives :

```text
/help
/context
/model
/goal
/profil
/compact
/secret
/secrets
/new
/exit
```

Choisir un profil de permission :

```bash
python3 -m bb9 --profile safe "bonjour"
python3 -m bb9 --profile limited "bonjour"
python3 -m bb9 --profile power "bonjour"
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
OPENAI_API_KEY=... python3 -m bb9 --provider openai-compatible --model gpt-4o-mini "bonjour"
```

Lister les providers connus :

```bash
bb9 --list-providers
```

Lister les modèles d'un provider :

```bash
OPENROUTER_API_KEY=... python3 -m bb9 --list-models openrouter
```

Configurer le provider actif en interactif :

```bash
python3 -m bb9
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
python3 -m bb9 --show-trace "/action demo"
```

Lister les agents Markdown disponibles :

```bash
python3 -m bb9 --list-agents
```

Lister les subagents Markdown d'un agent :

```bash
python3 -m bb9 --agent default --list-subagents
```

Lister les skills utilisateur disponibles :

```bash
python3 -m bb9 --list-skills
```

Lister les tools Markdown disponibles :

```bash
python3 -m bb9 --list-tools
```

Rafraichir les index Markdown des skills utilisateur et tools natifs :

```bash
python3 -m bb9 --refresh-indexes
```

Les index sont aussi régénérés automatiquement au lancement de `bb9`.

BB9 génère aussi un index des subagents de l'agent actif :

```text
~/.bb9/agents/<agent>/subagents/INDEX.md
```

Le subagent `default` sert de fallback pour une délégation bornée quand aucune spécialisation ne correspond mieux.
Le subagent `goal` sert de worker conventionnel pour `/goal` ; l'évaluateur critique reste dans le runtime.
Son `MODEL.md` peut cibler un modèle plus léger que l'agent principal, sans changer le provider ni les secrets.
Il peut aussi définir `ReasoningEffort`, par exemple `low` pour limiter le coût des itérations.

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

Un tool ou un skill est une archive Markdown autonome, avec backend optionnel. Les tools vivent dans l'archive BB9 ; les skills vivent dans `~/.bb9/skills/` et peuvent être copiés d'un BB9 à un autre.

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

Compacter le contexte court de la session REPL :

```text
/compact
```

BB9 compacte aussi automatiquement les anciens messages de session quand le contexte court devient trop long. Cette compaction reste interne à la session : elle ne modifie pas `MEMORY.md`.
La fenêtre de contexte du modèle actif est résolue depuis le cache local `~/.bb9/model-metadata.json`, une table connue embarquée, puis un fallback prudent. L'auto-compaction ne fait pas de requête web implicite.

Exécuter une commande de lecture via le tool `shell` :

```bash
python3 -m bb9 --shell "rg --files"
```

Vérifier le runtime minimal :

```bash
python3 -m unittest discover
```

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

Les signaux de veille utiles à la conception sont suivis dans `docs/external-signals.md`.
