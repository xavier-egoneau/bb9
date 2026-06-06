# Guardian

## Intention

Définir la couche de sécurité et de permission du système.

Le guardian protège l’utilisateur, les fichiers, les secrets et les actions externes contre les effets de bord non voulus.

Il est placé entre le modèle et les tools : le modèle peut proposer une action, mais le guardian décide si elle peut atteindre le gateway.

## Contrat

Le guardian doit :

- classifier les actions selon leur risque ;
- appliquer des profils de permission lisibles ;
- contrôler toute action avant accès au gateway ou à un tool ;
- demander validation quand nécessaire ;
- bloquer les actions interdites ;
- garder une trace des décisions sensibles ;
- rester compréhensible et prévisible.

Le guardian ne doit pas :

- décider de l’objectif produit ;
- devenir une boîte noire ;
- empêcher les actions sûres évidentes ;
- être contournable par le provider, le kernel, un subagent, un cron ou un mode continu ;
- stocker ou afficher des secrets.

## Position dans le système

Le chemin obligatoire est :

```text
décision structurée -> pre-action hook -> guardian -> gateway -> tool -> post-action hook
```

Aucun provider, kernel, subagent ou channel ne doit appeler un tool directement.

Le guardian intervient avant l'exécution. Le gateway peut revérifier qu'une action est autorisée, mais l'autorisation doit avoir été décidée avant qu'un tool soit appelé.

Le post-action hook agit après le tool pour vérifier l'observation, masquer les secrets et transmettre les événements utiles. Ce n'est pas une seconde décision du guardian.

## Concepts

Le guardian combine plusieurs informations :

- zone : workspace, trusted root, hors périmètre, chemin protégé ;
- risque intrinsèque de l'action ;
- profil de permission choisi par l'utilisateur ;
- règles absolues.

## Forme actuelle

La décision minimale du guardian est `GuardianDecision`.

Elle porte :

- `verdict` : `allow`, `ask` ou `block` ;
- `reason` : raison courte et affichable ;
- `action` : action éventuellement normalisée avant exécution.

Le coeur du guardian reste volontairement mince. `review_action` appelle d'abord la `review` du runtime de tool quand elle existe, puis applique un fallback simple basé sur `Action.risk` et le profil actif.

Conséquence importante : un tool qui produit des effets de bord ne doit pas compter sur le fallback générique. Il doit exposer une `review(action, context)` claire, utiliser le `RunContext` pour vérifier workspace/trusted roots/profil, et retourner une raison sans secret.

Ce choix garde le core lisible, mais il rend les contrats de tools importants : la sécurité fine vit au plus près de la capacité concrète.

## Profils de permission

Les profils règlent l'autonomie dans une zone de travail autorisée :

- `safe` : autonomie prudente ;
- `limited` : autonomie courante de travail ;
- `power` : autonomie forte, mais jamais sans règles absolues.

Dans un workspace ou trusted root, l'écriture normale est autorisée. Un agent qui doit demander à chaque écriture devient inutilisable.

Les profils ne remplacent pas les règles absolues.

Dans le REPL, le profil actif peut être changé pour la session courante avec :

```text
/profil
/profil safe
/profil limited
/profil power
```

Ce choix est sauvegarde dans le dossier user `~/.bb9/settings.json` et rechargé au prochain lancement. L'option `--profile` reste une surcharge explicite pour le lancement courant.

## Décisions

Le guardian retourne une décision simple :

- `allow` : l'action peut être exécutée ;
- `ask` : validation utilisateur nécessaire ;
- `block` : action interdite.

Dans le REPL, `ask` est présenté à l'utilisateur avant le gateway. L'utilisateur peut refuser, autoriser une fois, ou ajouter un chemin hors workspace aux trusted roots quand la demande concerne un périmètre local.

Dans le chat web, `ask` peut aussi être mémorisé explicitement. Cette mémorisation n'est jamais implicite après un simple `allow`.

## Approvals mémorisés

Les validations durables vivent dans `~/.bb9/approvals.json`.

Règles :

- une approval mémorisée porte sur une action exacte ;
- le fingerprint combine tool, paramètres publics et workspace ;
- les métadonnées internes de runtime ne font pas partie du fingerprint utilisateur ;
- les arguments stockés sont nettoyés pour éviter d'écrire des secrets ;
- une approval mémorisée ne transforme jamais un `block` en `allow` ;
- l'utilisateur doit choisir explicitement l'option de mémorisation.

Objectif : réduire les confirmations répétées sans élargir silencieusement le périmètre de permission.

## Zones

- `workspace` : périmètre courant du run ;
- `trusted root` : dossier autorisé durablement par l'utilisateur ;
- `outside` : hors périmètre connu, demande d'autorisation avant de devenir trusted root ;
- `protected` : zone système ou sensible, jamais autorisée automatiquement.

Règle :

```text
workspace / trusted root -> zone de travail
outside                  -> ask pour ajout aux trusted roots
protected                -> block
```

## Actions sensibles

Même dans un trusted root, certaines actions restent `ask` ou `block` :

- suppression ;
- modification massive ;
- secrets ;
- permissions ;
- commandes destructives ;
- réseau ;
- sortie de périmètre ;
- chemins protégés.

## Hooks

Le système peut prévoir des points de contrôle simples :

- avant exécution : préparer l'action, puis laisser le guardian classifier, demander confirmation ou bloquer ;
- après exécution : vérifier l'observation, masquer les secrets et tracer, sans réautoriser l'action ;
- avant arrêt : décider si la session doit être résumée, archivée ou oubliée.

Ces hooks ne doivent pas devenir un moteur de workflow caché.

Le contrat détaillé des hooks vit dans `docs/hooks.md`.

## Questions restantes

- Faut-il formaliser une taxonomie commune de risques par famille de tools, au-delà de `low`, `medium`, `high` et `forbidden` ?
- Quelles règles doivent rester centralisées dans le guardian plutôt que répétées dans les runtimes de tools ?
- Quand faut-il expirer, archiver ou oublier automatiquement une approval mémorisée ?
- Quels tests contractuels imposer à chaque tool exécutable pour garantir qu'il expose une `review` suffisante ?
