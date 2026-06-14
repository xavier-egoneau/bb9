---
name: local_auto_edit
description: Deleguer une tache de patch/review au runtime local optimise.
---

# Local Auto Edit

## Résumé

Deleguer une tache de patch/review au runtime local optimise.

## Quand l'utiliser

- L'utilisateur demande une modification de code qui peut etre traitee par le runtime local.
- L'agent veut comparer ou preparer un patch avec `local_runtime.cli auto-edit`.
- Une tache repo-edit doit beneficier du backend valide Gemma/llama.cpp ou d'un autre modele route par le runtime.

## Protocole

```text
BB9_ACTION local_auto_edit run prompt="Fix the bug without changing tests." file=app/main.py file=tests/test_main.py
BB9_ACTION local_auto_edit run prompt="Fix the bug" file=app/main.py test_command="python3 -m unittest discover -s tests" apply=true
BB9_ACTION local_auto_edit run workspace=/path/to/repo prompt="Review and patch" file=src/app.py model_alias=gemma4-e4b-gguf-q4km disable_thinking=true
```

## Entrées

- `prompt` : consigne transmise au runtime.
- `file` : fichier relatif ou absolu a inclure dans le contexte. Répétable.
- `workspace` : racine du repo. Par defaut, le workspace actif de BB9.
- `test_command` : commande a lancer apres application. Répétable.
- `apply=true` : applique les modifications et lance les tests. Sans `apply`, le runtime produit un dry-run/diff.
- `model_alias`, `backend`, `workload` : surcharges optionnelles du routeur runtime.
- `disable_thinking=true` : ajoute `--disable-thinking`.
- `timeout`, `startup_timeout`, `max_tokens`, `temperature`, `context_window` : surcharges optionnelles.
- `runtime_root` : chemin du projet `runtime`. Par defaut, `BB9_LOCAL_RUNTIME_ROOT` puis un dossier sibling `runtime`.

## Effets

Sans `apply`, le tool lance le runtime local et retourne son observation sans modifier le workspace.

Avec `apply=true`, le tool peut modifier des fichiers dans le workspace et lancer les commandes de test demandées.

## Permission

Dry-run : `allow` si le workspace cible est dans le workspace actif ou un trusted root.

`apply=true` : `allow` en `limited` et `power`; `ask` en `safe`.

Les workspaces hors périmètre demandent validation. Les chemins protégés sont bloqués.

## Règles

- Ne pas utiliser ce tool pour de simples lectures ou remplacements triviaux : préférer `files`.
- Toujours fournir au moins un `file`.
- Préférer `apply=false` quand l'utilisateur demande seulement une review ou une proposition de patch.
- Fournir des `test_command` quand `apply=true` et que le repo a une suite de tests connue.
- Le tool ne passe pas par un shell libre ; il appelle `python -m local_runtime.cli auto-edit` avec des arguments bornés.
