# Secret

## Résumé

Créer et lister des références de secrets locaux sans exposer les valeurs.

## Intention

Gérer des secrets nommés dans le store local BB9.

Le tool manipule des références stables comme `secret:OPENAI_API_KEY`. Il ne doit jamais retourner la valeur brute d'un secret.

## Quand l'utiliser

- L'utilisateur veut ajouter une API key, un token ou un secret local.
- Une config a besoin d'une référence de secret.
- Un provider ou un tool échoue car un secret manque.

## Entrées

- `op` : `set` ou `list`.
- `name` : nom logique du secret pour `set`.
- `value` : valeur sensible, fournie uniquement par le canal utilisateur local au moment de l'approbation.

## Backend

Le backend du tool vit avec lui :

```text
bb9/tools/secret/runtime.py
bb9/tools/secret/store.py
bb9/tools/secret/input_guard.py
bb9/tools/secret/cli.py
```

`cli.py` enregistre les commandes REPL et la capture locale via l'interface générique du CLI.

## Effets

Peut écrire un fichier secret local dans `~/.bb9/secrets/named/`.

## Permission

`ask` obligatoire pour toute écriture.

Dans le REPL, l'approbation ouvre une capture locale de la prochaine saisie utilisateur plutôt qu'une écriture immédiate.

Lister les noms de secrets peut être `allow`.

## Règles

- Ne jamais demander la valeur du secret dans la conversation provider.
- Ne jamais mettre la valeur dans une trace, un log, une observation ou un index.
- Retourner seulement une référence du type `secret:NOM`.
- Utiliser la référence dans la config, jamais la valeur brute.

## Méthode

1. Choisir un nom de variable stable et explicite, par exemple `OPENAI_API_KEY`, `OPENROUTER_API_KEY` ou `CALDAV_PASSWORD`.
2. Demander `BB9_ACTION secret add <NOM_DE_VARIABLE>`.
3. Laisser BB9 ouvrir la capture locale après validation utilisateur.
4. Attendre l'observation du store local.
5. Utiliser seulement la référence retournée, par exemple `secret:OPENAI_API_KEY`, dans la config concernée.

## Protocole

```text
BB9_ACTION secret add <NOM_DE_VARIABLE>
BB9_ACTION secret list
```

## Commandes REPL

```text
/secret list
/secret add <NOM_DE_VARIABLE>
/secrets
```
