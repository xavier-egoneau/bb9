---
activation: on-demand
---

# Plan

## Résumé

Découper une demande complexe en plan structuré, lisible et exécutable.

## Activation

Quand l'utilisateur demande `/plan`, un plan, un découpage de tâche, une
stratégie d'exécution ou une lecture des dépendances avant d'agir.

## Portée

Template global utilisateur. Un projet peut le spécialiser avec
`.bb9/skills/plan/SKILL.md`, qui prendra le dessus dans ce workspace.

## Commandes

`/plan ...` appelle ce skill par son nom d'archive. Aucune commande Python n'est
nécessaire tant que le Markdown suffit.

## Rôle

Tu transformes une demande en tâches bornées. Tu ne lances pas d'action métier
et tu ne délègues pas. Tu produis une structure que `/dev` ou un humain pourra
exécuter.

## Sortie

Produis un plan avec :

- objectif ;
- hypothèses ;
- tâches ;
- dépendances ;
- tâches parallélisables ;
- risques ;
- vérification.

Chaque tâche doit contenir :

- `id` ;
- `title` ;
- `goal` ;
- `context` ;
- `inputs` ;
- `expected_output` ;
- `done_criteria` ;
- `dependencies` ;
- `parallelizable` ;
- `suggested_worker` ;
- `permission_profile` ;
- `max_iterations`.

## Règles

- Une tâche doit être standalone.
- Une tâche sans contexte suffisant n'est pas délégable.
- Une dépendance doit être explicite.
- `parallelizable` doit être explicite.
- Deux tâches parallèles ne doivent pas modifier la même zone sans règle claire.
- Les inconnues bloquantes doivent être nommées.
- Le plan doit rester relisible par un humain.
