# Files

## Résumé

Lire et modifier des fichiers du workspace par opérations bornées.

## Quand l'utiliser

- L'utilisateur demande d'appliquer une modification dans un fichier.
- L'agent a déjà identifié le changement à faire.
- Une modification simple peut être exprimée par remplacement ou insertion.

## Protocole

```text
BB9_ACTION files replace path=index.html old="texte actuel" new="texte remplaçant"
BB9_ACTION files insert_before path=index.html marker="</head>" text="<link rel=\"stylesheet\" href=\"...\">"
BB9_ACTION files insert_after path=README.md marker="# Titre" text="Texte ajouté"
BB9_ACTION files write path=note.md text="# Note\n\nContenu"
```

## Entrées

- `path` : chemin du fichier dans le workspace.
- `old` / `new` : texte à remplacer et texte de remplacement.
- `marker` / `text` : texte repère et contenu à insérer.
- `all=true` : remplacer toutes les occurrences au lieu de la première.

## Effets

Peut créer ou modifier un fichier dans le workspace ou un trusted root.

## Permission

`allow` en `limited` et `power` dans le workspace ou un trusted root.

`ask` en `safe`.

Les chemins hors workspace/trusted roots demandent validation.

Les chemins protégés sont bloqués.

## Règles

- Ne pas supprimer de fichier.
- Ne pas écrire hors périmètre sans validation.
- Ne pas modifier un fichier si le marqueur ou le texte à remplacer est absent.
- Retourner une observation courte destinée à l'agent.
