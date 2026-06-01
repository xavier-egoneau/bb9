# Tasks

## Intention

`tasks` est la persistance métier minimale de BB9.

Elle sert à garder des tâches actionnables dans le temps, séparées des plans de
développement, des crons, des sessions et de la mémoire durable.

## Principe

- `CRON.md` dit quand déclencher quelque chose.
- `.bb9/plan.md` dit comment exécuter le plan courant.
- `~/.bb9/tasks/tasks.json` garde les tâches métier qui doivent survivre à la
  conversation.
- `TOOL.md` porte les règles et la politique d'usage.

Le runtime ne doit pas écrire d'état dans le Markdown source.

## Forme Minimale

Une tâche contient :

- `id` : identifiant stable.
- `title` : titre lisible en langage naturel.
- `prompt` : contexte ou consigne utile.
- `status` : `backlog`, `queued`, `running`, `done`, `failed` ou `paused`.
- `priority` : `high`, `med` ou `low`.
- `agent` : agent pressenti.
- `project_path` : projet concerné si applicable.
- `scheduled_for` : échéance métier optionnelle au format ISO.
- `events` : historique court des changements importants.

## Contrat Runtime

Le tool natif `tasks` expose :

```text
BB9_ACTION tasks create title="Relancer le dossier"
BB9_ACTION tasks list
BB9_ACTION tasks update id=task-12345678 status=done
```

`list` est une lecture locale. `create` et `update` écrivent un état durable et
demandent confirmation.

## Usage Conversationnel

Il n'y a pas de commande REPL `/tasks`.

L'utilisateur demande en langage naturel :

```text
Garde-moi une tâche pour relancer ce dossier lundi.
Quelles tâches ouvertes ai-je sur ce projet ?
Marque la relance provider comme terminée.
```

L'agent choisit alors d'utiliser `BB9_ACTION tasks ...` si c'est nécessaire.
L'observation du tool est technique et destinée à l'agent. L'utilisateur reçoit
un bilan rédigé en langage naturel par l'agent, pas la sortie brute du tool.

## Frontière Avec Cron

Un cron peut déclencher une intention, un dream ou une commande qui manipule une
tâche. Mais le cron ne devient pas le store métier.

Une tâche peut avoir `scheduled_for`, mais cette valeur ne crée pas à elle seule
une exécution automatique. Pour une exécution automatique, il faut un cron ou un
mode continu explicite.

Un `CRON.md` ne manipule pas directement `tasks`. S'il faut créer ou mettre à
jour une tâche à une cadence donnée, le cron déclenche une intention naturelle ;
l'agent décide ensuite d'utiliser le tool `tasks` si c'est pertinent.

## Frontière Avec Plan Et Dev

`/plan` et `/build` pilotent le travail courant dans `.bb9/plan.md`.

`tasks` garde des choses à tenir dans le temps : relances, suites, blocs de
travail, idées actionnables issues de dream ou d'une conversation.

Une tâche métier peut demander de lancer `/plan`, mais elle ne remplace pas le
plan.

## Frontière Avec Dream

Le dreaming peut proposer une action `task.create` quand il repère une suite
utile, sourcée et actionnable.

Cette action est matérialisée seulement pendant `/dream run` ou `/dream apply`.
`/dream preview` garde le plan en attente sans écrire de tâche.

Créer une tâche depuis dream ne lance pas d'agent, ne crée pas de cron et ne
notifie pas l'utilisateur. La tâche devient simplement visible dans le store
métier.

## Limites Volontaires

Cette première version ne gère pas encore :

- notifications ;
- retries ;
- locks multi-process ;
- workers asynchrones ;
- dashboard ;
- task board riche ;
- rappels conversationnels.

Ces capacités pourront se brancher autour du même contrat si elles deviennent
nécessaires.
