---
activation: on-demand, /plan, plan, découpage, stratégie d'exécution, implémenter, implémente, refactoriser, refactorise, corriger et tester, corrige et teste, plusieurs fichiers, feature, fonctionnalité, migration, architecture, workflow, longue tâche
name: plan
description: Découper une demande complexe en plan structuré, lisible et exécutable.
---

# Plan

## Résumé

Découper une demande complexe en plan structuré, lisible et exécutable.

## Activation

Quand l'utilisateur demande `/plan`, un plan, un découpage de tâche, une
stratégie d'exécution, une lecture des dépendances avant d'agir, ou une tâche
clairement multi-étapes : implémentation, refactor, correction plus tests,
plusieurs fichiers, feature, migration, architecture, workflow ou longue tâche.

## Portée

Template global utilisateur. Un projet peut le spécialiser avec
`.bb9/skills/plan/SKILL.md`, qui prendra le dessus dans ce workspace.

## Commandes

- `/plan ...` : produire un plan structuré avec tâches, dépendances et parallélisation.

`/plan` écrit toujours le plan courant dans `.bb9/plan.md`. Si ce fichier existe
déjà, il est écrasé par le nouveau plan.

## Rôle

Tu transformes une demande en tâches bornées. Tu ne lances pas d'action métier
et tu ne délègues pas. Tu produis une structure que `/build` ou un humain pourra
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
- `paths` ;
- `expected_output` ;
- `done_criteria` ;
- `dependencies` ;
- `parallelizable` ;
- `suggested_worker` ;
- `permission_profile` ;
- `max_iterations`.

Format Markdown cible :

```markdown
# BB9 Plan

Objective: ...

## Tasks

- [ ] T1 Lire le contexte
  worker: default
  parallelizable: false
  paths: docs/subagents.md
  depends:
  goal: Comprendre les responsabilités actuelles.
  context: Le parent a cadré le besoin.
  expected: Résumé des risques et fichiers concernés.
```

## Règles

- Une tâche doit être standalone.
- Une tâche sans contexte suffisant n'est pas délégable.
- Une dépendance doit être explicite.
- `parallelizable` doit être explicite.
- Deux tâches parallèles ne doivent pas modifier la même zone sans règle claire.
- Les inconnues bloquantes doivent être nommées.
- Le plan doit rester relisible par un humain.
