---
name: delegate
description: Lancer une tâche bornée dans un subagent du pool. Le parent reçoit un TaskResult synthétique.
---

# Delegate

## Résumé

Lancer une tâche bornée dans un subagent configuré de l'agent courant. Le parent
reçoit un `TaskResult` synthétique et reste responsable de la réponse finale à
l'utilisateur.

## Quand l'utiliser

- Le parent veut isoler une recherche, une vérification ou une génération bornée.
- La tâche peut être décrite comme une unité standalone avec objectif, contexte
  et sortie attendue.
- Le parent veut tester une action avec un profil de permission plus strict,
  par exemple `profile=safe`, sans modifier son propre profil.

## Protocole

```text
BB9_ACTION delegate run worker=dev id=T1 goal="Analyser" context="Contexte suffisant" expected="Résumé avec preuves" profile=safe
BB9_ACTION delegate run worker=research id=T2 title="Lire docs" goal="Identifier les risques" context="Projet BB9" expected="Liste de risques" paths=docs/agents.md,bb9/core/delegation.py tool_scope=dev
```

## Entrées

- `run` : lancer la délégation.
- `worker` : subagent à utiliser, `dev` par défaut.
- `id` : identifiant court de tâche.
- `title` : titre humain optionnel.
- `goal` : objectif autonome.
- `context` : contexte minimal nécessaire au subagent.
- `expected` ou `expected_output` : sortie attendue.
- `paths`, `inputs`, `done`, `dependencies` : listes séparées par virgules.
- `profile` : `safe`, `limited` ou `power`, toujours plafonné par le profil parent.
- `tool_scope` : `dev` par défaut. Le subagent reçoit seulement les tools de dev
  (`shell`, `files`, `browser`, `web`, `vision`).

## Effets

Exécute une loop synchrone avec le subagent dans une session courte
`delegation:<task-id>`. Le subagent utilise les mêmes frontières workspace,
gateway et guardian. Il ne parle pas directement à l'utilisateur. Les trusted
roots du parent ne sont pas hérités : le subagent travaille dans le workspace
actif et ne sort pas du dossier courant.

## Permission

Lecture seule par défaut. Les effets concrets demandés par le subagent passent
par les tools habituels et restent soumis au guardian. Le profil demandé ne peut
jamais dépasser le profil du parent.

## Règles

- Ne pas déléguer une tâche floue ou dépendante d'un contexte implicite.
- Ne pas utiliser `delegate` pour contourner une validation guardian.
- Ne pas déléguer récursivement : le contexte du subagent ne reçoit pas l'index
  des subagents.
- Ne pas donner au subagent des tools non-dev sans décision explicite.
- Résumer le `TaskResult` au parent avant toute réponse utilisateur.
