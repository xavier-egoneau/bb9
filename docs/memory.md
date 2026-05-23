# Memory

## Intention

Définir la mémoire durable du système sans la confondre avec la session, la trace ou un index technique.

La memory conserve des faits utiles et validés dans le temps. Elle doit rester compréhensible, éditable et contrôlée par l'humain.

## Contrat

La memory doit :

- contenir des faits durables, pas tout l'historique ;
- rester séparée de la session courte ;
- rester séparée des traces d'exécution ;
- être inspectable et modifiable par l'utilisateur ;
- pouvoir être résumée en Markdown ;
- garder la source ou la raison d'un fait quand c'est utile.

La memory ne doit pas :

- absorber automatiquement tous les documents, mails ou messages ;
- stocker des secrets bruts ;
- servir de permission implicite ;
- injecter des instructions cachées dans la loop ;
- devenir une base vectorielle avant usage réel.

## Ingestion

Toute ingestion durable doit être explicite ou configurée clairement.

Une synchronisation automatique peut exister plus tard, mais elle doit :

- être désactivable ;
- passer par le guardian si elle déclenche des actions ;
- distinguer collecte, résumé, validation et stockage ;
- éviter les canaux dormants d'instructions indésirables.

## Frontières

- La `session` garde le contexte court actif.
- La `trace` garde l'historique observable d'une exécution.
- Le `context-index` aide à retrouver du contexte dans des sources locales.
- La `memory` garde seulement les faits durables validés.

## Relation au kernel

La memory nourrit le kernel en contexte durable.

Elle ne doit pas être écrite librement par le kernel au départ : une proposition de mémoire durable doit être explicite, traçable et idéalement validée.

## Questions à résoudre

- Quel format minimal utiliser pour la mémoire durable ?
- Qui peut écrire dans la mémoire : utilisateur, loop, guardian, routine ?
- Faut-il une validation humaine avant tout ajout durable ?
- Comment supprimer ou corriger un fait ?
- Comment éviter qu'une mémoire ancienne devienne une instruction active dangereuse ?
