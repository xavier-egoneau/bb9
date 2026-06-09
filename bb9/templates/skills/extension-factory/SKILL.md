---
activation: on-demand, /extension-factory, /extension-factory-skill, /extension-factory-tool, /create-skill, /create-tool, créer un skill, créer un tool, nouveau skill, nouveau tool, extension BB9, méthode réutilisable, workflow réutilisable, automatiser une méthode, commande slash, action BB9_ACTION
---

# Extension Factory

## Résumé

Créer ou améliorer des skills et tools BB9 sans perdre la frontière entre extension utilisateur et capacité native.

## Activation

Quand l'utilisateur demande de créer, modifier, factoriser ou documenter un skill, un tool, une commande slash, une action `BB9_ACTION`, ou une méthode BB9 réutilisable.

Active-toi aussi sans commande explicite quand une demande révèle une méthode
qui devrait devenir durable : workflow répété, consigne locale stable, nouvelle
commande souhaitée, protocole d'action réutilisable, ou capacité qui manque
régulièrement à BB9.

## Portée

Template global utilisateur. Un projet peut le spécialiser avec `.bb9/skills/extension-factory/SKILL.md`, qui prendra le dessus dans ce workspace.

## Commandes

- `/extension-factory ...` : décider s'il faut créer un skill, un tool ou seulement documenter une méthode.
- `/extension-factory-skill ...` : créer ou améliorer un skill utilisateur BB9.
- `/extension-factory-tool ...` : créer ou améliorer un tool natif BB9.
- `/create-skill ...` : alias évident pour créer un skill utilisateur.
- `/create-tool ...` : alias évident pour créer un tool natif.

Ces commandes sont des méthodes Markdown. Elles ne remplacent pas le guardian ni les validations d'écriture.

## Rôle

Tu aides à créer des briques BB9 lisibles, portables et maintenables.

Tu dois être proactif : si une conversation fait émerger une règle stable ou
un workflow réutilisable, propose de le transformer en skill ou tool. Si le
bénéfice est clair et que l'écriture est autorisable par guardian, prépare la
brique au lieu d'attendre une formulation parfaite de l'utilisateur.

Avant d'écrire, tu identifies :

- le besoin utilisateur concret ;
- la brique la plus simple ;
- le lieu correct ;
- les fichiers déjà existants à relire ;
- les permissions nécessaires ;
- les tests ou vérifications minimales.

## Décision skill ou tool

Créer un skill quand :

- la brique personnalise la façon de travailler de l'utilisateur ;
- elle compose des tools existants ;
- elle doit être copiable entre installations BB9 ;
- elle vit naturellement dans `~/.bb9/skills/<name>/` ou `.bb9/skills/<name>/` ;
- elle peut rester majoritairement Markdown.

Créer un tool quand :

- la brique doit être livrée avec BB9 ;
- elle expose une capacité native générique ;
- elle a un protocole d'action clair ;
- elle doit passer par le guardian et le gateway ;
- elle vit dans `bb9/tools/<name>/`.

Ne crée pas de nouvelle brique quand :

- une section dans un skill ou tool existant suffit ;
- le besoin est un usage ponctuel ;
- le nom, le protocole ou les permissions ne sont pas encore clairs.

## Créer un skill

Pour un skill utilisateur, privilégie le tool natif `create_skill`.

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

## Créer un tool

Pour un tool natif, ne génère pas un runtime avant d'avoir un protocole clair.

Méthode :

- relire `docs/tools.md` et `docs/markdown-archives.md` ;
- regarder un tool proche dans `bb9/tools/` ;
- créer `bb9/tools/<name>/TOOL.md` avec intention, usage, protocole, sortie, permissions et limites ;
- ajouter `runtime.py` seulement si le tool agit réellement ;
- exposer au runtime uniquement `action_from_text`, `review` et `execute` quand c'est nécessaire ;
- garder le core Python comme helper, jamais comme contrat public ;
- écrire au moins un test ciblé si un runtime est ajouté ;
- lancer `python3.11 -m ruff check .` et les tests pertinents.

Structure minimale :

```text
bb9/tools/<name>/
  TOOL.md
```

Structure avec action :

```text
bb9/tools/<name>/
  TOOL.md
  runtime.py
```

Un `TOOL.md` doit rendre compréhensible le tool sans ouvrir le Python.

## Garde-fous

- Ne jamais stocker de secret dans un skill ou tool.
- Utiliser des références `secret:NOM`, `env:NOM` ou `file:chemin`.
- Ne pas coder de chemin absolu machine.
- Ne pas ajouter de framework agentique.
- Ne pas contourner guardian, gateway ou hooks.
- Ne pas créer de commande slash courte non namespacée sans raison forte.
- Ne pas dupliquer une capacité déjà portée par un tool natif.
- Ne pas rendre un tool responsable d'une décision agentique globale.

## Sortie attendue

Quand tu livres une nouvelle brique, explique :

- pourquoi c'est un skill ou un tool ;
- les fichiers créés ou modifiés ;
- les commandes ou actions disponibles ;
- les permissions impliquées ;
- les vérifications effectuées ;
- ce qui reste volontairement non implémenté.
