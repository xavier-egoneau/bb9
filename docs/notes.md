# Notes & Todos

## Intention

Donner à chaque agent un espace de travail personnel et durable, séparé du
workspace projet : des notes Markdown libres et une liste de tâches.

## Stockage

Tout vit dans le dossier de l'agent :

- notes : `agents_dir/<agent>/notes/<slug>.md`, une note par fichier ;
- todo : `agents_dir/<agent>/TODO.md`, une seule liste de cases à cocher
  (`- [ ] tâche` / `- [x] tâche`).

Les fichiers restent du Markdown lisible et éditable sans BB9. Le slug d'une
note est normalisé (kebab-case, accents repliés). La logique pure vit dans
`bb9/core/notes.py` et est réutilisée par le tool natif et par l'API web.

## Accès en contexte

Un bloc compact « Notes & Todos de l'agent » est injecté dans le contexte de
chaque tour (`RunContext.notes_context`, assemblé dans `context_runtime` et
rendu par le kernel). Il liste les tâches ouvertes et les titres de notes, pas
leur contenu complet : l'agent sait que ces notes existent et utilise le tool
`notes` pour lire le détail. Ce bloc est présent même en contexte « light ».

Les notes sont rattachées à l'agent canonique (`state.agent_name`), pas à un
subagent éphémère : l'interface web et le runtime partagent donc la même vue.

## Tool natif `notes`

Le tool `notes` (`bb9/tools/notes/`) donne à l'agent les opérations de lecture
et d'écriture sans accès au workspace : il résout le dossier de l'agent depuis
le contexte du tour. Voir `bb9/tools/notes/TOOL.md` pour le protocole complet
(`list`, `read`, `write`, `delete`, `todo-add`, `todo-done`, `todo-undone`,
`todo-edit`, `todo-remove`). Lecture en `allow`, écriture en `allow` sous
`limited`/`power` et `ask` en `safe`.

## Interface web

L'item de menu « Notes & todos » ouvre une modale en deux sections :

- en haut la liste de tâches (cocher, ajouter, éditer, supprimer) ;
- en bas les fichiers notes (créer, éditer le contenu en place, supprimer).

L'API expose `GET /api/notes` (todos + notes avec contenu),
`POST /api/notes/update` (write/delete) et `POST /api/todos/update`
(add/toggle/edit/remove). Les opérations ciblent l'agent actif du runtime web.

## Limites

- Les notes ne sont pas chiffrées : ne pas y stocker de secret (utiliser le
  tool `secret`).
- La todo est une liste unique par agent, volontairement simple ; pas de
  sous-listes ni d'échéances.
