# Config

## Intention

Définir comment le système charge ses paramètres sans perdre sa simplicité.

La config doit permettre d’adapter le système localement tout en gardant des valeurs par défaut lisibles.

## Contrat

La config doit :

- déclarer les chemins importants ;
- déclarer les providers disponibles ;
- déclarer les channels activés ;
- déclarer les tools autorisés ;
- déclarer les modes d'exécution autorisés ;
- déclarer le workspace par défaut ;
- déclarer ou charger les trusted roots locaux ;
- déclarer les index de contexte activés ;
- déclarer le niveau de logs local ;
- rester lisible par un humain.

La config ne doit pas :

- contenir de secrets bruts ;
- devenir un langage de programmation ;
- dupliquer les décisions documentées ;
- forcer une structure complexe trop tôt.

## Questions à résoudre

- Format : Markdown, YAML, TOML, JSON ?
- Où stocker la config locale ?
- Comment séparer config versionnée et config privée ?
- Comment référencer les secrets sans les exposer ?
- Quels paramètres sont vraiment nécessaires en phase 1 ?
- Comment représenter le choix entre exécution ponctuelle, mode continu et daemon optionnel ?
- Comment déclarer un index local sans imposer une dépendance lourde ?
- Comment configurer les logs sans bruit excessif ?

## Config locale actuelle

La premiere config locale concrete concerne les providers.

Le parcours d'installation standard est :

```bash
python3.11 -m bb9.install
# Windows :
py -3.11 -m bb9.install
```

Il crée :

- un fichier `.pth` dans le user-site Python pour exposer le dépôt ;
- un lanceur `bb9` dans le dossier de commandes utilisateur ;
- une entrée `PATH` utilisateur vers ce dossier quand c'est possible ;
- le dossier utilisateur `~/.bb9/` ;
- le dossier d'agents utilisateur `~/.bb9/agents/` ;
- le dossier de skills utilisateur globaux `~/.bb9/skills/` ;
- le dossier de skills locaux au workspace `.bb9/skills/` ;
- le dossier de goals utilisateur `~/.bb9/goals/` ;
- le dossier de secrets locaux `~/.bb9/secrets/`.
- le fichier de trusted roots utilisateur `~/.bb9/trusted-roots.md`.
- le fichier de goal courant `~/.bb9/goals/active.json`.
- le fichier de settings utilisateur `~/.bb9/settings.json`.

BB9 demande Python 3.11+. Le lanceur généré réutilise l'exécutable Python qui a lancé l'installateur. Un nouveau terminal peut être nécessaire pour récupérer le `PATH` mis à jour.

Installation standard Python, utile dans une venv ou via pipx :

```bash
python3.11 -m pip install -e .
```

Fichier utilisateur par defaut :

```text
~/.bb9/providers.json
```

Surcharge explicite possible :

```text
BB9_PROVIDER_CONFIG_PATH=/chemin/local/providers.json
bb9 --provider-config-path /chemin/local/providers.json
```

Ce fichier contient :

- le provider actif ;
- les providers configures localement ;
- leur URL de base ;
- leur methode d'authentification ;
- leur modele actif ;
- une reference de secret.

Les agents et subagents peuvent surcharger seulement le modele via leur `MODEL.md`. Cette surcharge reutilise le provider, l'authentification et les secrets actifs ; elle ne doit pas dupliquer de configuration sensible.

Les metadonnees de modele resolues automatiquement vivent dans :

```text
~/.bb9/model-metadata.json
```

Ce cache evite de recalculer la fenetre de contexte a chaque lancement.

Etat actuel : l'auto-compaction n'effectue pas de requete web implicite. Elle lit le cache, puis utilise la table connue embarquee ou un fallback prudent. Une future mise a jour automatique via web devra passer par une commande ou un tool explicite.

Les references de secrets acceptees au depart sont :

```text
env:OPENAI_API_KEY
env:OPENROUTER_API_KEY
file:/chemin/local/secret.txt
secret:OPENAI_API_KEY
```

Le fichier ne doit pas contenir de cle API brute. Les valeurs sensibles restent dans l'environnement, dans un fichier local non versionne ou dans le store local BB9.

La commande interactive `/model` est le premier outil utilisateur pour modifier cette config sans exposer de details internes. Pour une auth API, elle accepte les références `env:`, `file:` et `secret:`.

Pour l'auth web ChatGPT/Codex, les tokens sont stockes separement dans :

```text
~/.bb9/secrets/
```

La config provider ne garde qu'un chemin local vers ce fichier de token.

Les trusted roots sont ajoutés après validation humaine dans `~/.bb9/trusted-roots.md`, jamais dans le repo ni dans le workspace.

Le profil de permission courant est persistant dans :

```text
~/.bb9/settings.json
```

`/profil` le modifie durablement. `--profile` peut le surcharger pour un lancement sans modifier le choix sauvegarde.
