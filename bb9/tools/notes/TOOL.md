---
name: notes
description: Gérer les notes Markdown et la liste de tâches de l'agent dans son dossier.
---

# Notes

## Résumé

Gérer les notes Markdown et la liste de tâches (`- [ ] tâche`) de l'agent, stockées dans son dossier.

## Intention

Donner à l'agent un espace de travail personnel et durable : des notes libres et
une todo list, indépendants du workspace projet. Utile pour retenir un contexte,
suivre des tâches en cours, ou garder une mémoire de travail entre les tours.

## Quand l'utiliser

- L'utilisateur demande de noter, retenir, garder une trace de quelque chose.
- L'utilisateur parle de tâches, todo, choses à faire, rappel.
- L'agent veut suivre une liste de sous-tâches au fil d'une conversation.
- L'utilisateur demande de relire ou réviser ses notes.

## Stockage

- Notes : `agents_dir/<agent>/notes/<slug>.md`, une note par fichier Markdown.
- Todo : `agents_dir/<agent>/TODO.md`, une seule liste de cases à cocher.

Ces fichiers vivent dans le dossier de l'agent, pas dans le workspace projet.
Le tool résout le bon dossier depuis le contexte du tour ; l'agent n'a pas
besoin d'un accès en écriture au workspace pour cela.

## Contexte

Un résumé des notes et des tâches ouvertes est injecté dans le contexte de
chaque tour. L'agent sait donc qu'elles existent sans relire les fichiers, mais
doit utiliser `notes read <slug>` pour récupérer le contenu complet d'une note.

## Entrées

- `op` : `list`, `read`, `write`, `delete`, `todo-add`, `todo-done`, `todo-undone`, `todo-edit`, `todo-remove`.
- `slug` : nom court de la note (kebab-case).
- `text` : contenu d'une note ou texte d'une tâche.
- `title` : titre optionnel d'une note.
- `index` : indice de tâche (à partir de 0) tel qu'affiché par `notes list`.

## Permission

- Lecture (`list`, `read`) : `allow`.
- Écriture : `allow` en `limited` et `power`, `ask` en `safe`.

C'est l'espace privé de l'agent : aucune écriture n'y touche le workspace ni des
chemins protégés.

## Protocole

```text
BB9_ACTION notes list
BB9_ACTION notes read idees-projet
BB9_ACTION notes write idees-projet text="""# Idées

- explorer la piste A
- comparer B et C
""" title="Idées projet"
BB9_ACTION notes delete idees-projet
BB9_ACTION notes todo-add Préparer la démo de vendredi
BB9_ACTION notes todo-done 0
BB9_ACTION notes todo-undone 0
BB9_ACTION notes todo-edit 1 text="Relire le rapport avant lundi"
BB9_ACTION notes todo-remove 2
```

## Règles

- Une note par sujet, nom de fichier court et explicite.
- Ne pas dupliquer une tâche déjà présente dans la todo.
- Ne pas stocker de secret dans une note : utiliser le tool `secret`.
- Cocher une tâche terminée plutôt que la supprimer, sauf demande explicite.
- Relire avec `notes read` avant d'affirmer le contenu détaillé d'une note.

## Sortie attendue

- état à jour de la todo (tâches ouvertes et faites) ;
- liste des notes disponibles ;
- contenu d'une note quand elle est lue.
