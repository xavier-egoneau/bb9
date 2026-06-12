# Context Index

## Intention

Définir les index locaux qui aident le système à retrouver du contexte sans relire tout un projet à chaque tâche.

Un context-index est une carte générée : symboles, fichiers, dépendances, relations, recherches ou résumés techniques. Il sert à l'orientation, pas à décider.

## Contrat

Le context-index doit :

- être local et régénérable ;
- rester séparé de la memory durable ;
- indiquer sa source, sa fraîcheur et son périmètre ;
- exposer des requêtes bornées et idéalement en lecture seule ;
- réduire les lectures inutiles et le bruit de contexte ;
- pouvoir être ignoré si absent ou périmé.

Le context-index ne doit pas :

- devenir une source d'autorité supérieure au code réel ;
- stocker des secrets bruts ;
- déclencher des effets de bord ;
- remplacer les tests, la lecture ciblée ou la validation humaine ;
- imposer MCP ou une base vectorielle au départ.

## Usage envisagé

La première version est volontairement simple :

```text
workspace -> scan local -> .bb9/context-index.md -> contexte court du kernel -> action mieux cadrée
```

Elle génère un index Markdown local avec :

- fichiers ;
- dossiers ;
- fichiers de gouvernance détectés ;
- date de génération ;
- périmètre du workspace.

Le scan est volontairement borné et ignore les répertoires techniques ou de cache courants. Si le workspace est trop vaste, par exemple un dossier utilisateur complet, BB9 produit une carte partielle au lieu de bloquer l'interface.

Ce fichier appartient à la mémoire de travail locale de BB9 pour ce workspace. Il est régénérable et ne doit pas être traité comme une mémoire durable.

BB9 crée aussi un `.bb9/.gitignore` dans le workspace pour éviter de versionner cette mémoire locale par accident.

Elle ne génère pas encore de symboles, imports, appels ou dépendances. Ces informations restent futures et doivent être ajoutées seulement si l'usage le justifie.

## Frontières

- Le `context-index` est un outil de recherche structuré.
- Le `gateway` peut le construire ou l'interroger.
- Le `guardian` peut limiter son périmètre.
- La `memory` ne doit pas absorber automatiquement tout l'index.

## Relation au kernel

Le context-index nourrit le kernel en contexte court.

Le kernel le reçoit comme Markdown préparé. Il ne le construit pas lui-même, ne le persiste pas et ne le considère pas comme plus fiable que les fichiers sources.

## Questions à résoudre

- Faut-il exposer une commande utilisateur pour forcer ou afficher le rafraîchissement ?
- Quelles requêtes minimales servent la phase 1 ?
- Comment éviter qu'un index périmé induise l'agent en erreur ?
