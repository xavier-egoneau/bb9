# Memory

## Intention

Définir la mémoire durable du système sans la confondre avec la session, la trace ou un index technique.

La memory conserve des faits utiles et validés dans le temps. Elle doit rester compréhensible, éditable et contrôlée par l'humain.

BB9 utilise une mémoire SQL locale, structurée comme un petit graphe :

- des nœuds mémoire pour les faits durables ;
- des arêtes typées pour relier ces faits ;
- une recherche texte locale pour retrouver les souvenirs ;
- des scopes pour distinguer mémoire globale et mémoire projet.

## Contrat

La memory doit :

- contenir des faits durables, pas tout l'historique ;
- rester séparée de la session courte ;
- rester séparée des traces d'exécution ;
- être inspectable et modifiable par l'utilisateur ;
- pouvoir être résumée en Markdown ;
- garder la source ou la raison d'un fait quand c'est utile.
- distinguer les faits globaux des faits locaux à un projet ;
- permettre au dreaming de consolider, relier et corriger des faits.

La memory ne doit pas :

- absorber automatiquement tous les documents, mails ou messages ;
- stocker des secrets bruts ;
- servir de permission implicite ;
- injecter des instructions cachées dans la loop ;
- devenir une base vectorielle avant usage réel.

## Store SQL

La mémoire durable vit dans :

```text
~/.bb9/memory.db
```

Le store est SQLite et reste dans la bibliothèque standard Python.

Table principale :

```text
memory_nodes
- node_id
- content
- scope        # global | project
- project_path # chemin absolu si scope=project
- kind         # fact, preference, decision, project, ...
- tags
- source
- confidence
- created_at
- updated_at
```

Table de graphe :

```text
memory_edges
- edge_id
- source_id
- target_id
- relation
- weight
- source
- created_at
```

La recherche texte utilise FTS5 quand SQLite le permet, avec un fallback simple
si FTS5 n'est pas disponible.

## Scopes

`global` désigne les faits durables valables partout : préférences utilisateur,
repères de travail, décisions personnelles transversales.

`project` désigne les faits locaux à un projet précis : décisions techniques,
conventions, dette connue, suites à garder pour ce projet.

Le contexte actif d'une session est :

```text
mémoire globale + mémoire du projet courant
```

Les projets dormants gardent leurs nœuds mémoire sans être injectés tant qu'ils
ne sont pas le projet courant.

## Graphe

Les arêtes donnent au dreaming un moyen de conserver de la valeur croisée sans
réécrire tout en texte plat.

Exemples de relations :

- `supports` : un fait renforce un autre ;
- `contradicts` : un fait doit être vérifié contre un autre ;
- `derived_from` : un fait vient d'une session, d'une décision ou d'un document ;
- `belongs_to` : un fait est lié à un projet, skill, tool ou workflow ;
- `supersedes` : un fait remplace une ancienne formulation.

Le graphe reste un outil de consolidation et de navigation. Il ne devient pas
une permission implicite ni une instruction cachée.

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

## Relation au dreaming

Le dreaming lit la mémoire SQL comme matière première :

- nœuds globaux ;
- nœuds des projets concernés ;
- arêtes existantes ;
- sessions récentes ;
- `DREAM.md` des skills et tools actifs ;
- documents projet utiles.

Il produit ensuite des propositions structurées :

- ajouter un nœud ;
- remplacer ou supprimer un nœud périmé ;
- ajouter ou corriger une arête ;
- proposer une action métier sans l'exécuter.

Le moteur dreaming consolide et propose. L'application d'une opération mémoire
reste une écriture explicite dans le store, traçable et testable.

## Questions à résoudre

- Qui peut écrire dans la mémoire : utilisateur, loop, guardian, routine ?
- Faut-il une validation humaine avant tout ajout durable ?
- Comment supprimer ou corriger un fait ?
- Comment éviter qu'une mémoire ancienne devienne une instruction active dangereuse ?
