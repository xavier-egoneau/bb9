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
- distinguer événement temporaire, résumé de session et mémoire durable ;
- masquer les secrets et données sensibles.

La trace ne doit pas :

- devenir une mémoire long terme automatique ;
- remplacer les logs techniques du runtime ;
- exposer le raisonnement privé complet du modèle ;
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
```

Cette forme reste provisoire. Elle doit être validée par un usage réel de la loop.

## Frontières

- La `session` porte le contexte court actif.
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
