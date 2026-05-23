# Shell

## Résumé

Exécuter une commande shell bornée dans le workspace courant.

## Intention

Exécuter une commande shell bornée dans le workspace courant.

## Entrées

- `cmd` : commande à exécuter.
- `workdir` : répertoire de travail, par défaut le workspace courant.

## Effets

Peut lire le workspace et produire une sortie.

Peut avoir des effets de bord selon la commande demandée.

## Permission

`ask` par défaut.

Les commandes de lecture explicitement listées peuvent être `allow` dans le workspace ou un trusted root.

## Règles

- Respecter le workspace.
- Ne pas exposer de secrets.
- Retourner `stdout`, `stderr` et le code de sortie.
- Ne pas exécuter de commande destructive sans validation.
- Ne pas utiliser `shell=True` côté runtime.
- Bloquer les chemins protégés.
- Demander validation pour les chemins hors workspace/trusted roots.

## Commandes de lecture provisoirement connues

- `pwd`
- `ls`
- `find`
- `rg`
- `sed`
- `head`
- `tail`
- `cat`
