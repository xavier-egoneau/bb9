# Tools

## Intention

Définir les capacités natives livrées avec BB9.

Un tool est une archive autonome, générique et partageable. Il peut ajouter une capacité d'exécution, une méthode, une commande ou simplement un comportement attendu.

Un tool n'est jamais appelé directement par le modèle. Il est atteint via gateway après validation par le guardian.

Exemple : `shell`, `secret`, `caldav`, `create_skill`, `project-explorer` et `project-onboarding` sont des tools natifs.

## Contrat

Les tools doivent :

- vivre dans `bb9/tools/<name>/TOOL.md` quand ils sont déclarés en Markdown ;
- avoir un nom clair ;
- déclarer leurs entrées si elles existent ;
- déclarer leurs effets possibles si elles existent ;
- déclarer leurs permissions nécessaires si elles existent ;
- retourner une observation structurée quand ils ont un runtime ;
- être testables isolément quand ils ont un runtime ;
- rester portables, sans chemin local en dur.

Les tools ne doivent pas :

- prendre de décisions agentiques globales ;
- cacher les effets de bord ;
- exposer des secrets ;
- accepter d'entrée non validée par le guardian ;
- devenir des mini-agents autonomes.

## Archive autonome

Un tool peut contenir une part comportementale.

Un tool peut être :

- exécutable avec `runtime.py` ;
- documentaire sans backend ;
- mixte : protocole, permission, comportement attendu et backend.

Exemples :

- `shell` est un tool ;
- `caldav` est un tool ;
- `create_skill` est un tool ;
- `secret` est un tool ;
- `project-explorer` est un tool documentaire ;
- `project-onboarding` est un tool documentaire.

## Intégrations futures

Les tools peuvent plus tard être exposés via MCP ou une interface équivalente.

La forme interne minimale doit d'abord être claire : entrée, effet, permission, observation.

## Runtime autonome

Quand un tool a besoin de code dédié, ce code doit vivre avec le tool.

La porte d'entrée d'action est :

```text
bb9/tools/<name>/runtime.py
```

La porte d'entrée REPL est :

```text
bb9/tools/<name>/cli.py
```

Si le backend grossit un peu, il peut être partagé dans :

```text
bb9/tools/<name>/core.py
bb9/tools/<name>/core/core.py
```

`core.py` est alors importé par `runtime.py` ou `cli.py`. Il n'est pas le protocole public de l'archive.

`runtime.py` peut exposer :

- `action_from_text(text)` pour parser le protocole `BB9_ACTION <tool> ...` ;
- `review(action, context)` pour ses règles guardian spécifiques ;
- `execute(action)` pour produire une observation.

`cli.py` peut exposer :

- `register(cli)` pour ajouter des commandes ou comportements REPL.

Les commandes d'un tool appartiennent au tool. Elles doivent être lisibles dans
`## Commandes` de `TOOL.md` et enregistrées par `cli.py` seulement si une vraie
intégration REPL est nécessaire.

```markdown
## Commandes

- `/web` : ouvrir l'interface locale du tool.
```

Le core fournit seulement le chargeur générique. Il ne doit pas accumuler les implémentations métier des tools.

Un tool peut contenir dans son `TOOL.md` :

- quand l'utiliser ;
- sa méthode d'usage ;
- ses secrets requis ;
- son protocole `BB9_ACTION` ;
- ses limites.

Cette règle garde une archive autonome :

```text
bb9/tools/<name>/TOOL.md
bb9/tools/<name>/DREAM.md
bb9/tools/<name>/runtime.py
bb9/tools/<name>/cli.py
bb9/tools/<name>/core.py
bb9/tools/<name>/core/core.py
bb9/tools/<name>/<backend>.py
```

## Extension CLI

Un tool peut exposer une extension REPL via :

```text
bb9/tools/<name>/cli.py
```

S'il contient `register(cli)`, le REPL l'appelle au démarrage.

Pour les tools natifs, ce code fait partie de l'archive BB9. Pour les skills utilisateur, le même mécanisme existe mais il relève de la confiance locale de l'utilisateur.

Le tool peut alors enregistrer :

- des commandes slash ;
- des intercepteurs d'entrée utilisateur ;
- des handlers de validation guardian ;
- des lignes de contexte affichées par `/context` ;
- une capture locale temporaire via le CLI.

Le CLI reste un hôte générique. Il ne doit pas importer directement les fichiers métier des tools.

Un skill séparé n'est créé que si l'utilisateur veut enrichir son propre comportement local plutôt que modifier l'archive BB9.

## Activation

Par défaut, un agent reçoit tous les tools disponibles dans `bb9/tools/`.

Un agent peut désactiver certains tools avec :

```text
~/.bb9/agents/<name>/TOOLS_DISABLED.md
```

Le fichier reste en Markdown et contient une liste à puces de noms de tools :

```markdown
- shell
```

La désactivation limite ce que le kernel peut présenter au modèle comme tools disponibles. Elle ne remplace pas le guardian.

## Questions à résoudre

- Quels tools atomiques indispensables : `shell`, `read-file`, `write-file`, autre ?
- Comment documenter les permissions nécessaires ?
- Comment gérer les erreurs et sorties partielles ?
- Quels tools indispensables pour la phase 1 ?
