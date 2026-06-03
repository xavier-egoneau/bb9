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

Les commandes d'écriture simples explicitement listées, comme `touch` et `mkdir`,
peuvent être `allow` dans le workspace ou un trusted root.

Les pipelines de lecture simples peuvent être normalisés sans `shell=True`, par exemple
`cat fichier | head -20` devient `head -20 fichier`.
Les pipelines composés uniquement de commandes de lecture connues, comme
`find ... | sort`, `find ... | grep ... | head -20` ou `rg ... | head -20`,
sont exécutés comme processus chaînés sans passer par un shell.
Les pipelines non supportés sont bloqués avant validation humaine.

Les chaînes `&&` composées uniquement de lectures connues peuvent être exécutées
séquentiellement sans `shell=True`, par exemple `git status --short && ls`.

`python3 -m http.server <port>` et `python -m http.server <port>` sont traités
comme serveurs locaux de prévisualisation : démarrage en arrière-plan, bind
forcé à `127.0.0.1` si absent, sans `shell=True`.
Si le port demandé est occupé ou ne répond pas correctement, le tool essaie les
ports suivants et retourne l'URL réellement disponible.

Les commandes destructives explicitement demandées dans le workspace ne sont pas interdites par principe : elles demandent validation.

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
- `grep`
- `sort`
- `sed`
- `head`
- `tail`
- `cat`
- `git status`
- `git log`
- `git diff`
- `git show`
- `git branch`
- `git rev-parse`
- `git ls-files`

## Commandes d'écriture simples provisoirement connues

- `touch`
- `mkdir`

## Commandes longues reconnues

- `python3 -m http.server <port>`
- `python -m http.server <port>`
