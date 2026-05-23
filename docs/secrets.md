# Secrets

## Intention

Définir une manière élégante, sûre et minimale de gérer les secrets du système.

Un secret est une donnée sensible utilisée par le système sans être exposée dans le code, les logs, les traces, les prompts ou les fichiers de contexte.

## Contrat

La gestion des secrets doit :

- ne jamais stocker de secret brut dans le dépôt ;
- référencer les secrets par nom ou alias stable ;
- séparer configuration publique et valeurs sensibles ;
- permettre au gateway d'accéder aux secrets seulement au moment nécessaire ;
- empêcher les secrets d'apparaître dans les observations, traces et erreurs ;
- rester portable entre environnements.

La gestion des secrets ne doit pas :

- dépendre d'un fournisseur unique ;
- encourager les `.env` partagés ou commités ;
- transmettre les secrets au kernel si seule l'action exécutée en a besoin ;
- rendre le setup local inutilement compliqué.

## Store local

La première gestion concrète des secrets utilise des références nommées :

```text
secret:OPENAI_API_KEY
secret:OPENROUTER_API_KEY
secret:CALDAV_PASSWORD
```

Les valeurs vivent localement dans :

```text
~/.bb9/secrets/named/
```

La config ne garde que la référence `secret:NOM`. Le provider, le kernel, les traces et les index ne doivent jamais recevoir la valeur brute.

## Tool et skill

Le tool atomique est `secret`.

Il peut :

- créer ou remplacer un secret nommé ;
- lister les références disponibles.

Son runtime agentique vit dans le tool :

```text
bb9/tools/secret/runtime.py
bb9/tools/secret/store.py
bb9/tools/secret/input_guard.py
bb9/tools/secret/cli.py
```

Toute écriture de secret est `ask` obligatoire.

En REPL, l'approbation n'écrit pas immédiatement le secret. Elle ouvre une capture locale :

```text
BB9_ACTION secret add OPENAI_API_KEY
guardian -> ask
utilisateur -> accepte
REPL -> secret attendu
utilisateur -> saisit la valeur
Secret stored: secret:OPENAI_API_KEY
```

Quand un secret est attendu, la prochaine saisie utilisateur est interceptée localement et ne passe pas par le provider. La commande `/cancel` annule la capture.

## Interception opportuniste

Le REPL peut détecter une entrée utilisateur qui ressemble à un secret avant tout appel provider.

Dans ce cas :

- le message brut n'est pas envoyé au provider ;
- BB9 propose un nom de secret ;
- l'utilisateur confirme le stockage local ;
- seule la référence `secret:NOM` est affichée ;
- la session ne garde pas la valeur brute.

Cette interception couvre les erreurs humaines, par exemple une clé collée directement dans la conversation.

Cette détection reste secondaire. Le parcours fiable est la procédure explicite de capture ouverte par `BB9_ACTION secret add <NOM_DE_VARIABLE>`.

Le tool `secret` décrit lui-même la méthode :

- choisir un nom stable ;
- demander `BB9_ACTION secret add <NOM_DE_VARIABLE>` ;
- utiliser la référence retournée dans une config ;
- garder toute écriture de config comme action séparée.

Les tools qui dépendent de secrets, comme `caldav`, doivent déclarer leurs noms attendus et renvoyer vers le tool `secret` si une référence manque. Ils ne doivent pas collecter eux-mêmes les valeurs sensibles.

## Questions à résoudre

- Le gateway résout-il les secrets ou délègue-t-il à un secret store ?
- Comment masquer les secrets dans les logs et observations ?
- Comment gérer les secrets absents ou expirés proprement ?
- Faut-il supporter env vars, fichiers locaux, keyring système, ou plusieurs backends ?
