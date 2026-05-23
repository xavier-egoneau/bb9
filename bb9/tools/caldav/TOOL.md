# CalDAV

## Résumé

Lire et diagnostiquer un agenda CalDAV local via `vdirsyncer` et `khal`.

## Intention

Accéder à un calendrier local déjà synchronisé ou synchronisable sans exposer les secrets CalDAV.

## Quand l'utiliser

- L'utilisateur parle d'agenda, calendrier, rendez-vous ou disponibilité.
- L'utilisateur demande un briefing du jour.
- L'utilisateur mentionne CalDAV, iCloud, `khal` ou `vdirsyncer`.
- Le setup calendrier semble incomplet.

## Runtime

L'implémentation autonome du tool vit dans :

```text
bb9/tools/caldav/runtime.py
```

`bb9` ne contient pas la logique CalDAV. Le gateway charge ce runtime depuis le dossier du tool.

## Entrées

- `op` : `doctor`, `agenda` ou `maintenance`.
- `days` : nombre de jours à lire pour `agenda` et `maintenance`, par défaut `7`.
- `sync` : synchroniser avant lecture pour `agenda`, par défaut `true`.
- `operation` : `refresh`, `discover`, `sync` ou `verify` pour `maintenance`.
- `timeout_seconds` : timeout local, par défaut `30`.

## Effets

- `doctor` lit l'état local des binaires et configs.
- `agenda` peut lancer `vdirsyncer sync`, puis lit les événements via `khal`.
- `maintenance` peut lancer `discover`, `sync` et une vérification de lecture.

## Permission

- `doctor` : `allow`.
- `agenda` : `ask` en `safe`, `allow` en `limited` ou `power`.
- `maintenance` : `ask`.

## Secrets requis

Le tool ne demande jamais de secret brut.

Une configuration CalDAV peut nécessiter :

- `CALDAV_URL`
- `CALDAV_USERNAME`
- `CALDAV_PASSWORD`

Utiliser des références locales :

```text
secret:CALDAV_URL
secret:CALDAV_USERNAME
secret:CALDAV_PASSWORD
```

Si un secret manque, utiliser le tool `secret` :

```text
BB9_ACTION secret add CALDAV_PASSWORD
```

## Règles

- Ne jamais exposer de secret CalDAV dans la conversation, la trace ou l'observation.
- Ne pas modifier le calendrier par shell interactif.
- Ne pas prétendre connaître l'agenda sans lecture fraîche.
- Si le setup est incomplet, retourner le blocage concret.

## Méthode

1. Pour vérifier l'installation, demander `BB9_ACTION caldav doctor`.
2. Avant de répondre sur l'agenda réel, demander une lecture fraîche avec `BB9_ACTION caldav agenda days=7`.
3. Si le setup manque de credentials, utiliser le tool `secret` avec `BB9_ACTION secret add CALDAV_URL`, `BB9_ACTION secret add CALDAV_USERNAME` et `BB9_ACTION secret add CALDAV_PASSWORD`.
4. Utiliser seulement les références `secret:NOM` dans la configuration.
5. Si une maintenance est nécessaire, demander explicitement `BB9_ACTION caldav maintenance refresh`.

## Sortie attendue

- événements pertinents aujourd'hui et dans les prochains jours ;
- conflits ou transitions serrées ;
- préparations nécessaires ;
- blocage concret si le setup échoue.

## Protocole

```text
BB9_ACTION caldav doctor
BB9_ACTION caldav agenda days=7
BB9_ACTION caldav agenda days=2 sync=false
BB9_ACTION caldav maintenance refresh
```
