# Channels

## Intention

Définir les surfaces d’entrée/sortie du système.

Un channel est une interface par laquelle un utilisateur ou un autre système échange avec l’agent.

Le kernel peut appeler un channel adapter, mais le channel reste responsable du transport concret.

## Contrat

Les channels doivent :

- recevoir une entrée ;
- transmettre une intention au système ;
- restituer une réponse ;
- rester séparés de la logique décisionnelle du kernel ;
- indiquer le mode d'exécution demandé quand c'est utile ;
- pouvoir être remplacés sans changer le cœur.

Les channels ne doivent pas :

- contenir la logique décisionnelle ;
- exécuter directement des actions métier ;
- imposer une dépendance lourde au noyau ;
- mélanger transport, rendu et raisonnement.

## REPL

Le REPL est un channel local interactif.

Il fournit une interface d'extension minimale aux tools natifs :

- ajout de commandes slash ;
- interception locale d'une entrée avant provider ;
- traitement interactif d'un verdict guardian `ask` ;
- capture locale temporaire d'une valeur utilisateur ;
- ajout de lignes dans `/context`.

Un tool déclare ces extensions dans `bb9/tools/<name>/cli.py` avec une fonction `register(cli)`.

Le REPL ne doit pas importer les fichiers métier d'un tool un par un. Il découvre les extensions via le chargeur générique.

## Questions à résoudre

- Premier channel : CLI, HTTP local, fichier inbox ?
- Comment représenter une session ?
- Comment gérer le streaming ou les réponses longues ?
- Comment distinguer utilisateur local, API externe et routine planifiée ?
- Faut-il un protocole commun pour tous les channels ?
- Comment exposer clairement le choix entre exécution ponctuelle, mode continu et daemon optionnel ?
