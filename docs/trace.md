# Trace

## Intention

Définir ce que le système garde comme historique observable d'une exécution.

Une trace sert à comprendre ce qui s'est passé : intention reçue, décisions prises, actions demandées, validations, observations, erreurs et résultat final.

## Contrat

La trace doit :

- relier une intention, une session, une décision, une action et une observation ;
- rester lisible par un humain ;
- aider à auditer les effets de bord ;
- enregistrer les décisions sensibles du guardian ;
- fournir une matière affichable aux surfaces sous forme de trace visible ;
- distinguer événement temporaire, résumé de session et mémoire durable ;
- masquer les secrets et données sensibles.

La trace ne doit pas :

- devenir une mémoire long terme automatique ;
- remplacer les logs techniques du runtime ;
- exposer le raisonnement privé complet du modèle ;
- confondre processus visible et raisonnement privé brut ;
- stocker des secrets bruts ;
- devenir si détaillée qu'elle rend le système illisible ;
- être nécessaire pour comprendre les contrats du système.

## Forme minimale envisagée

Une entrée de trace pourrait contenir :

```text
time
session_id
source
event_type
summary
references
risk
result
display_hint
```

Cette forme reste provisoire. Elle doit être validée par un usage réel de la loop.

## Frontières

- La `session` porte le contexte court actif.
- L'`historique visible` conserve le fil relisible par l'utilisateur et les
  references d'artefacts.
- Le rôle visible `process` décrit la progression UX, sans être réinjecté tel
  quel au provider.
- La `trace` garde l'historique observable d'une exécution.
- La `memory` garde seulement les faits durables validés.
- Le `context-index` aide à retrouver du contexte local, mais ne remplace pas la trace.
- Le `guardian` ajoute les décisions de permission sensibles.
- Le `gateway` ajoute les observations liées aux effets de bord.
- Les `logs` diagnostiquent le runtime sans remplacer la trace.

## Questions à résoudre

- Quel format utiliser : Markdown, JSONL, SQLite ou autre ?
- Où stocker les traces locales ?
- Quelle granularité garder sans bruit excessif ?
- Comment relier trace, session, mémoire et fichiers modifiés ?
- Que faut-il masquer automatiquement ?
- Combien de temps garder les traces ?

## Historique Visible Et Artefacts

La trace n'est pas le fil utilisateur. Une execution peut produire beaucoup
d'evenements utiles au debug, alors que l'utilisateur a besoin d'un historique
plus calme : messages visibles, notifications importantes et artefacts.

BB9 garde donc une brique separee :

```text
~/.bb9/visible-history.db
```

Elle stocke les messages visibles et les artefacts (`diff`, `tool_trace`,
`image`, `report`, `file`, `screenshot`, `note`). La trace reste libre d'etre
plus technique sans polluer la surface utilisateur.

Un diff visible vient de la trace ou du controle de version, mais il est rendu
comme artefact de conversation. Il doit rester rattache au tour qui a modifie
les fichiers, avec resume global, compteurs `+/-` et details par fichier
repliables quand le channel le permet.

Une surface peut afficher une trace visible des tools : outil en cours, commande
ou cible, statut, durée, résultat court et détail repliable. Elle ne doit pas
afficher les prompts internes, secrets, ni raisonnement privé brut.

La première forme persistée est un artefact `tool_trace` par tour. Il liste les
tools exécutés, leur statut et un résumé court, puis laisse l'agent produire le
bilan naturel.

La trace terminée ne remplace pas l'état live. Pendant l'exécution, une surface
doit afficher que l'agent est actif, puis signaler explicitement chaque tool en
cours d'utilisation. Après exécution, le marqueur live devient une trace
terminée ou disparaît au profit d'un artefact `tool_trace`.
