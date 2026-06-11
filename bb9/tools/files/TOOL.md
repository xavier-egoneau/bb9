---
name: files
description: Lire et modifier des fichiers du workspace par opérations bornées.
---

# Files

## Résumé

Lire et modifier des fichiers du workspace par opérations bornées.

## Quand l'utiliser

- L'utilisateur demande d'appliquer une modification dans un fichier.
- L'agent a déjà identifié le changement à faire.
- Une modification simple peut être exprimée par remplacement ou insertion.

## Protocole

```text
BB9_ACTION files read path=src/app.js
BB9_ACTION files read path=src/app.js offset=100 limit=50
BB9_ACTION files replace path=index.html old="texte actuel" new="texte remplaçant"
BB9_ACTION files insert_before path=index.html marker="</head>" text="<link rel=\"stylesheet\" href=\"...\">"
BB9_ACTION files insert_after path=README.md marker="# Titre" text="Texte ajouté"
BB9_ACTION files write path=note.md text="# Note\n\nContenu"
BB9_ACTION files write note.md text="# Note\n\nContenu"
BB9_ACTION files write path=page.html text="""<!doctype html>
<html>...</html>
"""
BB9_ACTION files write page.html <<'EOF'
<!doctype html>
<html>...</html>
EOF
BB9_ACTION files write_many [{"path":"index.html","content":"<!doctype html>..."},{"path":"style.css","content":":root {...}"}]
BB9_ACTION files write_many files=[{"path":"index.html","content":"<!doctype html>..."},{"path":"style.css","content":":root {...}"}]
BB9_ACTION files {"ops":[{"op":"write","path":"index.html","content":"<!doctype html>..."},{"op":"write","path":"style.css","content":":root {...}"}]}
BB9_ACTION files {"path":"note.md","content":"# Note\n\nContenu"}
```

## Entrées

- `path` : chemin du fichier dans le workspace.
- `offset` / `limit` : ligne de début et nombre de lignes pour une lecture partielle.
- `old` / `new` : texte à remplacer et texte de remplacement.
- `marker` / `text` : texte repère et contenu à insérer.
- `content`, `contents` ou `body` : alias acceptés pour `text` en écriture.
- `b64` : contenu UTF-8 encodé en base64, utile pour de très gros fichiers.
- Le chemin peut être donné avec `path=...` ou, pour les opérations simples, comme premier argument après l'opération (`write note.md text=...`).
- `write` accepte aussi une forme heredoc bornée (`write note.md <<'EOF' ... EOF`) pour les contenus multi-lignes. Elle reste une action `files`, donc le guardian applique les mêmes règles de permission.
- Un objet JSON avec `path` et `content`/`text` sans `op` est interprété comme `write`; avec `path` seul, comme `read`.
- `write_many` : liste JSON de fichiers avec `path` et `text`/`content`, utile pour livrer plusieurs fichiers ensemble. Le préfixe `files=` est accepté comme alias de `items=`. Un objet JSON `{ "ops": [{ "op": "write", ... }] }` est aussi accepté et normalisé en `write_many`.
- `all=true` : remplacer toutes les occurrences au lieu de la première.

## Effets

`read` : retourne le contenu du fichier dans l'observation. Lecture partielle possible via `offset`/`limit`.

Peut créer ou modifier un fichier dans le workspace ou un trusted root.

L'exécution utilise le workspace du `RunContext`, pas le dossier courant
accidentel du processus Python.

## Permission

`read` : `allow` dans tous les profils (lecture seule, non-destructif).

`write`/`replace`/`insert` : `allow` en `limited` et `power` dans le workspace ou un trusted root. `ask` en `safe`.

Les chemins hors workspace/trusted roots demandent validation.

Les chemins protégés sont bloqués.

## Règles

- Ne pas supprimer de fichier.
- Ne pas écrire hors périmètre sans validation.
- Ne pas modifier un fichier si le marqueur ou le texte à remplacer est absent.
- Retourner une observation courte destinée à l'agent.
