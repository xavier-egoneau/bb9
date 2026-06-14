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

Pour une demande de bilan, critique, analyse ou état du projet, le sujet est le
workspace/repo courant : fichiers, code, docs, tests, configuration projet, git
status et observations de tools. Les index BB9 (`Tools Index`, `Skills Index`,
`Subagents Index`), les budgets de contexte, l'identité d'agent et le protocole
`BB9_ACTION` décrivent tes moyens de travail ; ils ne sont pas des faits du
projet. Ne les cite que si l'utilisateur demande explicitement un bilan de BB9,
de l'agent ou de ses capacités.

Le plan est le livrable de cadrage. Il ne doit pas contenir une suite de tâches
qui consiste seulement à analyser, explorer, réfléchir, faire un autre plan ou
proposer plus tard des pistes. Si l'utilisateur demande des évolutions, le plan
doit déjà nommer des évolutions concrètes et exécutables, avec chemins probables,
résultat attendu et critère de vérification. Une tâche valide fait avancer
l'objectif par un changement, une vérification ou un livrable concret ; elle ne
prépare pas seulement un futur plan.

`max_iterations` borne le nombre d'actions outil du worker pendant `/build`.
Utilise `1` pour une action simple, `2` à `4` pour une tâche qui doit lire puis
modifier ou vérifier. Si le champ est absent, le runtime utilise `4` par
compatibilité ; davantage doit rester exceptionnel.

Le champ `worker:` doit contenir `default` ou un nom présent dans `Subagents
Index`. Les tools et skills ne sont pas des workers. Par exemple
`project-explorer` peut être mentionné comme capacité utile dans le contexte
d'une tâche, mais ne doit pas apparaître dans `worker:`.

## Sortie

Produis uniquement le Markdown du plan, sans fence ```markdown et sans
commentaire hors plan. Avant de finir, relis ton plan comme un contrat
exécutable par `/build`.

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

Format Markdown obligatoire pour chaque tâche :

```markdown
# BB9 Plan

Objective: ...

## Tasks

- [ ] T1 Lire le contexte
  worker: default
  parallelizable: false
  paths: docs/subagents.md
  depends:
  max_iterations: 2
  goal: Comprendre les responsabilités actuelles.
  context: Le parent a cadré le besoin.
  expected_output: Résumé des risques et fichiers concernés.
  done_criteria: Le résumé cite les fichiers lus.
```

## Règles

- Une tâche doit être standalone.
- Une tâche sans `expected_output` explicite est invalide et bloquera `/build`.
- Une tâche sans contexte suffisant n'est pas délégable.
- Une dépendance doit être explicite.
- `parallelizable` doit être explicite.
- Deux tâches parallèles ne doivent pas modifier la même zone sans règle claire.
- Les inconnues bloquantes doivent être nommées.
- N'invente pas de chemins. Utilise seulement des chemins visibles dans le
  workspace, dans l'index de contexte ou dans la demande. Si le chemin est
  incertain, crée d'abord une tâche de vérification avec `paths:` vide et un
  `expected_output` de type "liste des fichiers réels à modifier".
- Si l'objectif vise BB9 lui-même mais que le workspace courant ne contient pas
  les fichiers BB9 attendus, le plan doit commencer par une tâche de blocage ou
  de changement de workspace, pas par des edits sur des chemins imaginaires.
- Evite les titres génériques comme `Analyser le workspace`, `Explorer le projet`
  ou `Proposer des améliorations` quand ils ne produisent pas directement un
  livrable concret.
- N'utilise pas de nom de tool ou de skill dans `worker:` ; utilise `default` si
  aucun subagent spécialisé n'est disponible.
- Le plan doit rester relisible par un humain.
