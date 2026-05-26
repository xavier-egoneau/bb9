# Historique Visible Et Artefacts

## Intention

Conserver ce que l'utilisateur peut relire sans le confondre avec la session
courte, la trace runtime ou la memoire durable.

L'historique visible sert aux surfaces futures : CLI enrichie, web, Telegram,
dashboard externe, notifications, rapports et reprise humaine.

## Contrat

L'historique visible doit :

- garder les messages visibles utilisateur, assistant, process, notification et systeme ;
- rester append-only ;
- masquer les secrets probables avant persistence ;
- relier chaque message a une session et, si possible, a un workspace ;
- attacher des artefacts structurés au message qui les rend visibles ;
- pouvoir s'exporter en Markdown portable.

L'historique visible ne doit pas :

- remplacer la session courte injectee au provider ;
- devenir la memoire durable ;
- stocker le raisonnement prive du modele ;
- porter la logique d'un dashboard ;
- imposer un daemon ou un canal concret.

## Artefacts

Un artefact est une preuve ou sortie structurée liee a une execution :

```text
Artifact
- id
- kind: diff | tool_trace | image | report | file | screenshot | note
- title
- path
- source
- created_at
- metadata
```

Le fichier ou la donnee concrete reste a son emplacement naturel. L'artefact
stocke la reference, le type et les metadonnees minimales pour le retrouver et
le rendre.

### Artefact `diff`

Un artefact `diff` represente les changements produits pendant un tour visible.
Il est rattache au message assistant qui annonce ou recapitule le travail.

Ses metadonnees doivent permettre un rendu pliable par defaut :

- `files_changed` : nombre de fichiers modifies ;
- `insertions` : total de lignes ajoutees ;
- `deletions` : total de lignes supprimees ;
- `files` : liste ordonnee de fichiers avec `path`, `insertions`,
  `deletions` et, si disponible, une reference vers les hunks ;
- `default_collapsed` : `true` pour indiquer l'intention de rendu.

Le diff complet peut vivre dans un fichier `.diff`, dans une trace locale ou
dans une representation interne. L'historique visible garde la reference et les
compteurs, pas forcement tout le patch inline.

### Artefact `tool_trace`

Un artefact `tool_trace` represente les tools effectivement utilises pendant un
tour. Il garde une liste courte d'entrees avec le nom du tool, le statut et un
resume humain.

Il ne doit pas contenir l'observation brute complete, les prompts internes, les
secrets ou les donnees sensibles. Il sert a montrer ce que l'agent a fait, pas a
remplacer son recap final.

## Rendu Conversationnel

L'historique visible doit pouvoir porter des éléments affichables par plusieurs
surfaces :

- blocs de commandes avec copie ;
- processus visible synthétique ;
- listes de tâches Markdown ;
- traces visibles de tools ;
- diffs ;
- fichiers et rapports produits ;
- confirmations et notifications ;
- erreurs résumées avec détail technique repliable.

Le rendu exact dépend du channel, mais le service rendu doit rester aligné.

Les diffs doivent être affichés comme des cartes ou blocs repliables par tour
quand le channel le permet. Le premier niveau montre le résumé global et les
fichiers touchés ; chaque fichier se déplie séparément. Les surfaces plus
simples exportent la même information en Markdown ou en fichier attaché.

Le rôle `process` sert au flux visible de progression. Il peut être affiché dans
le chat, exporté en Markdown ou dégradé en résumé selon le canal. Il ne fait pas
partie de la session courte injectée au provider.

L'historique visible conserve les états terminés. Les états live comme
`activity_indicator` ou `live_tool_use` appartiennent au channel pendant
l'exécution ; ils deviennent persistables seulement lorsqu'ils produisent un
message `process`, une notification ou un artefact comme `tool_trace`.

## Store Initial

La premiere implementation utilise :

```text
~/.bb9/visible-history.db
```

Tables :

- `visible_messages` : messages visibles append-only ;
- `artifacts` : artefacts rattaches a un message visible ou conserves seuls.

Ce store est un etat runtime local. Les contrats restent en Markdown ; SQLite
porte l'historique vivant.

## CLI

Apres chaque tour utilisateur/assistant, BB9 ecrit :

- le contexte court dans `sessions.db` ;
- le fil visible dans `visible-history.db`.

La commande REPL :

```text
/history
/history 30
```

exporte les derniers messages visibles en Markdown.

## Frontieres

- `sessions.db` : contexte court compactable et consolidable par dream.
- `visible-history.db` : fil humain relisible et surfaces futures.
- `memory.db` : faits durables consolides.
- `trace` : evenements runtime observables d'un tour.
- `logs` : diagnostic technique.

Le dreaming peut utiliser l'historique visible plus tard, mais ne doit jamais le
promouvoir tel quel en memoire durable. Il doit extraire des faits utiles,
sourcés et non secrets.
