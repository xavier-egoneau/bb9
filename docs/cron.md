# Cron

## Intention

Définir l'exécution planifiée du système sans transformer le projet en plateforme d'automatisation lourde.

Le cron permet de lancer des intentions récurrentes ou différées : briefing quotidien, maintenance, veille, synthèse, vérification périodique.

BB9 utilise un seul concept : une archive `CRON.md` décrit une intention
différée ou récurrente. Une tâche planifiée unitaire et une routine récurrente
ont donc la même forme, avec un `Mode` différent.

## Contrat

Le cron doit :

- déclencher une intention explicite à un moment défini ;
- gérer les deux modes `once` et `recurring` ;
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

## Archive `CRON.md`

Forme cible :

```text
~/.bb9/cron/<name>/CRON.md
```

Sections attendues :

- `Résumé` : description courte affichable dans les index ;
- `Activation` : `active` ou `paused` ;
- `Agent` : agent cible, par défaut `default` ;
- `Mode` : `once` ou `recurring` ;
- `Schedule` : planification humaine ;
- `Command` : commande interne BB9 optionnelle ;
- `Intention` : message envoyé au runtime quand le cron se déclenche ;
- `Limites` : garde-fous à injecter avec l'intention ;
- `Retry` : politique déclarative de relance en cas d'échec ;
- `Après exécution` : politique déclarative après un run ;
- `Notification` : préférence de notification.
- `History` : politique de conservation de l'historique runtime.

Exemple unitaire :

```markdown
# CRON.md

## Résumé

Relancer Xavier sur la décision provider.

## Activation

active

## Agent

default

## Mode

once

## Schedule

At: 2026-05-28 14:00
Timezone: Europe/Paris

## Intention

Demande à Xavier s'il veut trancher entre provider API key et OAuth web.

## Après exécution

Keep: archived
Notify: yes

## Retry

Attempts: 0

## Notification

Mode: always
Channel: local

## History

Mode: summary
Limit: 20
```

Exemple récurrent :

```markdown
# CRON.md

## Résumé

Briefing du matin.

## Activation

active

## Agent

default

## Mode

recurring

## Schedule

Time: 08:30
Days: monday, tuesday, wednesday, thursday, friday
Timezone: Europe/Paris

## Intention

Prépare un briefing court avec les priorités du jour.

## Limites

- Ne pas modifier de fichiers.
- Ne pas lancer de commandes longues.

## Après exécution

Keep: active
Notify: yes

## Retry

Attempts: 2
Delay: 10m

## Notification

Mode: errors
Channel: local

## History

Mode: summary
Limit: 20
```

Exemple dreaming planifié :

```markdown
# CRON.md

## Résumé

Consolidation nocturne de la mémoire.

## Activation

active

## Mode

recurring

## Schedule

Time: 02:00
Days: daily
Timezone: Europe/Paris

## Command

/dream run nightly

## Notification

Mode: errors
Channel: local
```

## Planification

Pour `once`, `Schedule` doit contenir :

```text
At: YYYY-MM-DD HH:MM
Timezone: Europe/Paris
```

Pour `recurring`, `Schedule` doit contenir :

```text
Time: HH:MM
Days: monday, wednesday, friday
Timezone: Europe/Paris
```

`Days` accepte aussi :

- `daily` : tous les jours ;
- `weekdays` : lundi à vendredi ;
- `weekend` : samedi et dimanche.

Si `Days` est absent sur un cron `recurring`, BB9 le traite comme `daily`.

Le support des noms français peut être ajouté, mais les noms anglais restent la
forme canonique pour éviter les ambiguïtés.

## Politiques

Les politiques restent déclaratives dans `CRON.md`. Elles ne transforment pas le
scheduler en worker spécialisé.

`Retry` indique seulement comment BB9 doit relancer une intention échouée :

```text
Attempts: 2
Delay: 10m
```

`Attempts` désigne le nombre de relances après l'échec initial. `Delay` est un
délai en minutes ou en forme courte comme `10m`.

`Notification` indique quand le runtime doit signaler un résultat :

```text
Mode: errors
Channel: local
```

`Mode` accepte :

- `none` : ne pas notifier ;
- `errors` : notifier seulement les échecs ;
- `always` : notifier succès et échecs.

`Channel` reste pour l'instant déclaratif. Le premier branchement CLI affiche
une notification locale textuelle ; les transports système peuvent venir plus
tard comme adapters.

`History` contrôle l'historique runtime :

```text
Mode: summary
Limit: 20
```

`Mode: none` désactive l'historique. `Mode: summary` garde des résumés courts
des exécutions. `Limit` borne le nombre d'entrées conservées par cron.

## Source et état

`CRON.md` décrit l'intention durable et la planification désirée.

L'état d'exécution calculé ne doit pas être réécrit dans l'archive source :

- `last_run` ;
- `next_run` ;
- erreurs ;
- lock ;
- historique des runs ;
- statut temporaire d'exécution.

Ces données vivent dans la persistance runtime. Ainsi, un scheduler peut tourner
sans salir les fichiers Markdown utilisateur à chaque tick.

## Runner pur

Le kernel expose une première couche de calcul sans effet de bord :

- `CronSpec` : contrat chargé depuis `CRON.md` ;
- `CronRunState` : état runtime minimal transmis par la persistance ;
- `cron_is_due(...)` : indique si le cron doit se déclencher maintenant ;
- `next_run_after(...)` : calcule la prochaine occurrence future ;
- `due_crons(...)` : filtre une liste de crons actifs et dus.

Cette couche ne lance pas d'agent, n'écrit pas l'historique et ne modifie pas
les archives. Elle permet de brancher ensuite le même contrat sur une commande
manuelle, un mode continu explicite ou un scheduler externe.

Pour les routines récurrentes, BB9 déclenche seulement l'occurrence du jour
courant. Il ne rattrape pas automatiquement une occurrence ancienne manquée :
cette politique pourra être ajoutée explicitement plus tard si elle devient
nécessaire.

Si un `Timezone` est déclaré et que le runtime fournit un `now` timezone-aware,
le calcul est fait dans ce fuseau. Si `now` est naïf, BB9 le traite comme une
heure déjà locale au cron.

## Commande runtime

Le CLI expose un branchement explicite :

```text
/cron status
/cron due
/cron tick
```

`/cron status` liste les archives découvertes, leur mode, leur état calculé et
leur prochaine occurrence. `/cron due` affiche les crons dus maintenant.

`/cron tick` déclenche seulement les crons dus. Il transforme chaque archive en
intention BB9, puis passe par la loop normale : kernel, guardian, gateway,
trace et provider actif. Le tick n'est donc pas un raccourci d'exécution caché.

L'état technique est conservé dans `~/.bb9/cron-state.json` :

- `lastRun` ;
- `lastError` ;
- `locked`.
- `failureCount` ;
- `retryAt` ;
- `history`.

Cet état sert au runtime et reste séparé des archives `CRON.md`.

## Commandes Internes

Un cron peut porter une section `Command` au lieu d'une section `Intention`.
Dans ce cas, BB9 exécute une commande interne explicitement supportée par le
runtime, sans demander au provider d'interpréter le texte du cron.

Commandes supportées :

```text
/dream run <name>
```

Cela permet de planifier le dreaming avec le système cron existant, sans créer
un scheduler spécialisé pour cette feature.

Règles :

- `Command` reste déclaratif et lisible dans `CRON.md` ;
- la planification reste portée par `Schedule` ;
- la logique de consolidation reste portée par `DREAM.md` ;
- les commandes sensibles futures devront être explicitement ajoutées au
  runtime, pas exécutées par défaut.

Pour créer ou modifier une tâche métier depuis un cron, utiliser plutôt une
section `Intention` en langage naturel. L'agent décidera ensuite d'appeler le
tool `tasks` si c'est nécessaire.

## Mode continu

Le mode continu est acceptable s'il est lancé explicitement par l'utilisateur et reste interrompable.

Le daemon au démarrage de l'ordinateur peut être proposé plus tard comme option de confort, après stabilisation du mode continu et des permissions.

Une routine planifiée ne doit jamais devenir une permission permanente implicite.

## Questions à résoudre

- Faut-il commencer avec le cron système, un scheduler Python, ou un fichier de routines ?
- Comment éviter deux exécutions concurrentes ?
- Où écrire l'historique des runs ?
- Comment notifier l'utilisateur sans multiplier les canaux ?
