---
activation: on-demand
---

# Dev

## Résumé

Exécuter un plan BB9 en respectant dépendances, parallélisation et retours de tâches.

## Activation

Quand l'utilisateur demande `/dev`, l'exécution d'un plan, la coordination de
tâches ou la préparation d'une délégation contrôlée.

## Portée

Template global utilisateur. Un projet peut le spécialiser avec
`.bb9/skills/dev/SKILL.md`, qui prendra le dessus dans ce workspace.

## Commandes

`/dev ...` appelle ce skill par son nom d'archive. Aucune commande Python n'est
nécessaire tant que le Markdown suffit.

## Rôle

Tu exécutes le plan. Tu gardes la trace canonique dans la conversation, tu
lances seulement les tâches dont les dépendances sont satisfaites et tu
collectes les résultats.

## Exécution

Pour chaque tâche :

- vérifier les dépendances ;
- choisir le worker local ou subagent suggéré ;
- transmettre un objectif explicite et le contexte strictement nécessaire ;
- lancer les tâches parallélisables sans attendre les autres tâches indépendantes ;
- attendre les tâches qui bloquent la suite ;
- résumer l'état utile à l'utilisateur.

## TaskResult

Chaque tâche doit revenir avec :

- `task_id` ;
- `status: done | error` ;
- `summary` ;
- `changed` ;
- `observed` ;
- `blockers` ;
- `evidence` ;
- `next_suggestion`.

## Règles

- `done` exige une preuve ou une observation.
- `error` doit expliquer le blocage concret.
- Une dépendance en erreur bloque les tâches dépendantes.
- Aucun subagent ne reçoit une mission vague.
- Aucun droit implicite n'est ajouté par `/dev`.
- Les actions concrètes restent soumises au guardian et au gateway.
- Le parent tient l'utilisateur au courant dans le chat canonique.
