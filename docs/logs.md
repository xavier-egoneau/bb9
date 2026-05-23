# Logs

## Intention

Définir les logs techniques du runtime sans les confondre avec la trace agentique.

Les logs servent à diagnostiquer le système : erreurs, warnings, démarrage, appels internes, durée, chemins utilisés. La trace sert à comprendre une exécution agentique.

## Contrat

Les logs doivent :

- utiliser la bibliothèque standard Python au départ ;
- rester sobres par défaut ;
- aider à diagnostiquer les erreurs sans exposer de secrets ;
- pouvoir être activés en mode plus verbeux localement ;
- rester distincts de la trace.

Les logs ne doivent pas :

- stocker le raisonnement privé complet du modèle ;
- remplacer la trace ;
- contenir des secrets bruts ;
- devenir une base d'audit produit ;
- imposer une dépendance externe.

## Niveaux provisoires

- `DEBUG` : diagnostic local détaillé.
- `INFO` : événements runtime utiles mais rares.
- `WARNING` : situation récupérable ou comportement inattendu.
- `ERROR` : échec d'une action ou d'un appel.

## Frontières

- La `trace` raconte le run agentique.
- Les `logs` diagnostiquent le runtime.
- Le `gateway` peut logger les erreurs techniques, mais la trace garde l'observation utile.
- Le `guardian` peut logger une décision sensible sans exposer son contenu secret.

## Questions à résoudre

- Faut-il loguer vers stderr seulement au départ ?
- Quel format minimal utiliser ?
- Où configurer le niveau de logs ?
- Comment masquer les secrets dans les messages ?
- Quels événements méritent un log mais pas une trace ?
