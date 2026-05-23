# Cron

## Intention

Définir l'exécution planifiée du système sans transformer le projet en plateforme d'automatisation lourde.

Le cron permet de lancer des intentions récurrentes ou différées : briefing quotidien, maintenance, veille, synthèse, vérification périodique.

## Contrat

Le cron doit :

- déclencher une intention explicite à un moment défini ;
- rester séparé de la loop agentique ;
- enregistrer les exécutions et leurs résultats ;
- gérer les erreurs sans spammer ni relancer en boucle ;
- permettre de désactiver facilement une tâche planifiée ;
- fonctionner sans exiger un daemon au démarrage.

Le cron ne doit pas :

- contenir de logique métier ;
- contourner le guardian ou les permissions ;
- exécuter une action sensible sans validation préalable ;
- imposer un mode always-on.

## Mode continu

Le mode continu est acceptable s'il est lancé explicitement par l'utilisateur et reste interrompable.

Le daemon au démarrage de l'ordinateur peut être proposé plus tard comme option de confort, après stabilisation du mode continu et des permissions.

Une routine planifiée ne doit jamais devenir une permission permanente implicite.

## Questions à résoudre

- Faut-il commencer avec le cron système, un scheduler Python, ou un fichier de routines ?
- Comment représenter une routine planifiée en Markdown ou config ?
- Comment éviter deux exécutions concurrentes ?
- Où écrire l'historique des runs ?
- Comment notifier l'utilisateur sans multiplier les canaux ?
