---
activation: on-demand, /extension-factory, /extension-factory-skill, /create-skill, créer un skill, nouveau skill, créer une capacité, nouvelle capacité, extension BB9, méthode réutilisable, workflow réutilisable, automatiser une méthode, commande slash, action BB9_ACTION
name: extension-factory
description: Créer ou améliorer des skills utilisateur BB9 ; les tools natifs ne sont pas des extensions utilisateur.
---

# Extension Factory

## Résumé

Créer ou améliorer des skills utilisateur BB9. Les extensions utilisateur sont
toujours des skills, y compris quand elles portent une vraie capacité avec du
Python : tout vit dans le dossier skills.

## Activation

Quand l'utilisateur demande de créer, modifier, factoriser ou documenter un skill, une capacité, une commande slash, une action `BB9_ACTION`, ou une méthode BB9 réutilisable.

Active-toi aussi sans commande explicite quand une demande révèle une méthode
qui devrait devenir durable : workflow répété, consigne locale stable, nouvelle
commande souhaitée, protocole d'action réutilisable, ou capacité qui manque
régulièrement à BB9.

## Portée

Template global utilisateur. Un projet peut le spécialiser avec `.bb9/skills/extension-factory/SKILL.md`, qui prendra le dessus dans ce workspace.

## Commandes

- `/extension-factory ...` : décider s'il faut créer un skill ou seulement documenter une méthode.
- `/extension-factory-skill ...` : créer ou améliorer un skill utilisateur BB9.
- `/create-skill ...` : alias évident pour créer un skill utilisateur.

Ces commandes sont des méthodes Markdown. Elles ne remplacent pas le guardian ni les validations d'écriture.

## Rôle

Tu aides à créer des briques BB9 lisibles, portables et maintenables.

Tu dois être proactif : si une conversation fait émerger une règle stable ou
un workflow réutilisable, propose de le transformer en skill. Si le
bénéfice est clair et que l'écriture est autorisable par guardian, prépare la
brique au lieu d'attendre une formulation parfaite de l'utilisateur.

Avant d'écrire, tu identifies :

- le besoin utilisateur concret ;
- la brique la plus simple ;
- le lieu correct ;
- les fichiers déjà existants à relire ;
- les permissions nécessaires ;
- les tests ou vérifications minimales.

## Frontière skill / tool natif

Toute extension utilisateur est un skill. Un skill peut porter une vraie
capacité : `runtime.py` pour une action contrôlée, `core/` pour un backend
Python partagé. Il vit toujours dans `~/.bb9/skills/<name>/` ou
`.bb9/skills/<name>/`, jamais ailleurs.

Les tools natifs (`bb9/tools/<name>/`) sont l'équipement de base livré avec
BB9. Ils ne se créent pas et ne se suppriment pas depuis une conversation ou
l'interface : ils s'activent ou se désactivent par agent dans la gestion des
agents, et certains se paramètrent là (secrets référencés). Si une capacité
mérite vraiment de devenir native, c'est une contribution au dépôt BB9, pas une
extension utilisateur : dis-le explicitement et reste sur un skill en
attendant.

Ne crée pas de nouvelle brique quand :

- une section dans un skill existant suffit ;
- le besoin est un usage ponctuel ;
- le nom, le protocole ou les permissions ne sont pas encore clairs.

## Créer un skill

Pour un skill utilisateur, privilégie le tool natif `create_skill`. Il écrit
uniquement dans les dossiers skills (user ou workspace) : c'est le contrat, ne
cherche pas à le contourner avec `files` ou `shell` vers d'autres dossiers.

Protocole :

```text
BB9_ACTION create_skill draft <nom>
BB9_ACTION create_skill draft <nom> local
BB9_ACTION create_skill draft <nom> global
BB9_ACTION create_skill draft <nom> cli
BB9_ACTION create_skill draft <nom> runtime
BB9_ACTION create_skill draft <nom> core
```

Méthode :

- choisir un nom court en kebab-case ;
- décider `local` si le skill est propre au workspace courant, sinon `global` ;
- commencer par `SKILL.md` seul ;
- ajouter `cli.py` seulement pour une commande REPL humaine réelle ;
- ajouter `runtime.py` seulement pour une action contrôlée ;
- ajouter `core.py` seulement si `cli.py` ou `runtime.py` partagent un backend ;
- relire `docs/skills.md` et `docs/markdown-archives.md` si la structure est incertaine ;
- régénérer l'index des skills après création.

Un bon `SKILL.md` contient au minimum :

- `Résumé` ;
- `Activation` ;
- `Intention` ou `Rôle` ;
- `Quand l'utiliser` ;
- `Comportement attendu` ;
- `Commandes` si le skill expose une commande ;
- `Actions` si le skill propose un protocole ;
- `Permissions` ;
- `Tests manuels`.

## Garde-fous

- Ne jamais stocker de secret dans un skill.
- Utiliser des références `secret:NOM`, `env:NOM` ou `file:chemin`.
- Ne pas coder de chemin absolu machine.
- Ne pas ajouter de framework agentique.
- Ne pas contourner guardian, gateway ou hooks.
- Ne pas créer de commande slash courte non namespacée sans raison forte.
- Ne pas dupliquer une capacité déjà portée par un tool natif.
- Ne jamais écrire dans `bb9/tools/` ni proposer de supprimer un tool natif.

## Sortie attendue

Quand tu livres une nouvelle brique, explique :

- pourquoi c'est un skill (et, si une capacité native serait préférable, pourquoi c'est une contribution au dépôt et pas une extension) ;
- les fichiers créés ou modifiés ;
- les commandes ou actions disponibles ;
- les permissions impliquées ;
- les vérifications effectuées ;
- ce qui reste volontairement non implémenté.
