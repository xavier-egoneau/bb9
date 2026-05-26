# Tasks

## Résumé

Persister des tâches métier simples que BB9 doit tenir dans le temps, sans les
confondre avec les plans de développement, les crons ou la mémoire durable.

## Activation

on-demand

## Quand l'utiliser

- L'utilisateur veut que BB9 garde une tâche à faire plus tard.
- Une routine, un cron ou un dream produit une suite concrète à traiter.
- Une tâche doit survivre à la session courante.
- Il faut suivre un statut métier simple : backlog, queued, running, done,
  failed ou paused.

## Comportement attendu

- Créer des tâches courtes, autonomes et actionnables.
- Garder le titre lisible en langage naturel.
- Retourner une observation technique à l'agent ; l'utilisateur reçoit ensuite
  un bilan naturel rédigé par l'agent.
- Utiliser `scheduled_for` seulement pour une échéance métier ; la cadence reste
  dans `CRON.md`.
- Ne pas écrire l'état runtime dans le Markdown source.
- Ne pas remplacer `/plan` et `/dev` : un plan courant vit dans `.bb9/plan.md`,
  une tâche métier vit dans `~/.bb9/tasks/tasks.json`.
- Ne jamais stocker de secret brut dans une tâche.

## Protocole

```text
BB9_ACTION tasks create title="Relancer le dossier" prompt="Contexte utile"
BB9_ACTION tasks create "Relancer le dossier" priority=high agent=default scheduled_for=2026-06-01T09:00:00+02:00
BB9_ACTION tasks list
BB9_ACTION tasks list status=queued
BB9_ACTION tasks list include_done=false
BB9_ACTION tasks update id=task-12345678 status=done
BB9_ACTION tasks update id=task-12345678 status=paused prompt="Bloque sur validation humaine"
```

## Commandes

Aucune commande REPL native.

L'utilisateur parle en langage naturel. L'agent décide si le tool `tasks` est
utile, l'appelle via `BB9_ACTION tasks ...`, puis répond à l'utilisateur en
langage naturel.

## Données

- Le contrat vit dans cette archive `TOOL.md`.
- L'état runtime vit dans `~/.bb9/tasks/tasks.json`.
- Chaque tâche garde un historique d'événements court.

## Permissions

- `list` est une lecture locale.
- `create` et `update` écrivent un état durable et demandent confirmation.
- Une tâche planifiée n'autorise pas son exécution future sans guardian.

## Limites

- Ce tool ne lance pas d'agent.
- Ce tool ne planifie pas de tick.
- Ce tool ne notifie pas l'utilisateur.
- Ce tool ne remplace pas un dashboard ou un task board riche.
- Les retries, locks et workers viendront autour du même contrat si besoin.
