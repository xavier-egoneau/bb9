# Skills

## Intention

Définir les extensions utilisateur partageables de BB9.

Un skill est une archive autonome, générique et copiable d'un BB9 à un autre. Il peut ajouter une capacité, une méthode, une action, une commande ou un comportement attendu. Il peut aussi avoir des fichiers backend si le besoin apparaît.

La différence principale avec un tool est son lieu et son statut :

```text
bb9/tools/           -> capacités natives livrées avec BB9
~/.bb9/skills/       -> skills globaux utilisateur
.bb9/skills/         -> skills locaux du workspace courant
```

## Contrat

Les skills doivent :

- vivre dans `~/.bb9/skills/<name>/SKILL.md` ;
- pouvoir vivre dans `.bb9/skills/<name>/SKILL.md` quand ils sont propres à un projet ;
- décrire quand ils s’activent ;
- modifier le comportement de l’agent ou fournir une capacité d'action de façon lisible ;
- rester en Markdown autant que possible ;
- être portables ;
- éviter les chemins locaux en dur ;
- pouvoir être copiés dans le dossier skills d'un autre BB9.

Les skills ne doivent pas :

- remplacer le kernel ;
- contenir de secrets ;
- devenir une collection de prompts flous ;
- devenir un canal dormant d'instructions non revues ;
- exécuter du code local non relu via `runtime.py`, `cli.py` ou `core.py`.

## Frontière avec tools

Un skill et un tool peuvent tous les deux porter une capacité, une action ou un comportement attendu.

La frontière pratique est :

- un tool est livré dans l'archive BB9 ;
- un skill appartient à l'utilisateur et vit dans `~/.bb9/skills/`.

Les tools natifs peuvent donc porter leur méthode d'usage dans `TOOL.md`. Les skills servent à enrichir, personnaliser ou étendre BB9 localement. La différence est le lieu et le statut, pas la nature profonde de la capacité.

## Archive

Structure minimale :

```text
~/.bb9/skills/<name>/SKILL.md
.bb9/skills/<name>/SKILL.md
```

Structure avec extension REPL :

```text
~/.bb9/skills/<name>/SKILL.md
~/.bb9/skills/<name>/cli.py
~/.bb9/skills/<name>/runtime.py
~/.bb9/skills/<name>/core.py
~/.bb9/skills/<name>/DREAM.md
```

`SKILL.md` porte le contrat lisible. `cli.py` est optionnel et peut exposer `register(cli)` pour ajouter une commande REPL. `runtime.py` est optionnel et peut exposer `action_from_text`, `review` et `execute` pour une action contrôlée. `core.py` est optionnel et sert de backend partagé importé par `cli.py` ou `runtime.py`.

Le CLI charge les extensions de skills avec le même principe que les extensions de tools : le noyau reste hôte générique, le skill enregistre ce dont il a besoin.

Si une commande slash inconnue correspond au nom d'un skill actif, le REPL la transmet comme intention au kernel. Cela permet à un skill Markdown pur comme `plan` ou `dev` d'être appelé avec `/plan ...` ou `/build ...` sans fournir de `cli.py`.

Si un skill expose plusieurs commandes, ces commandes doivent être déclarées
en liste à puce dans `## Commandes` de son `SKILL.md` :

```markdown
## Commandes

- `/plan` : produire un plan structuré.
- `/plan-review` : variante de revue de plan.
```

Une commande déclarée dans `## Commandes` peut servir d'alias Markdown pur vers
le skill. Si elle demande une intégration REPL réelle, elle est enregistrée par
le `cli.py` du skill. Les commandes appartiennent à l'archive qui les porte,
comme pour les tools.

Les paragraphes explicatifs et les exemples placés après la liste ne sont pas
des commandes déclarées.

Les surfaces n'affichent que le premier token slash comme commande. Une syntaxe
comme `/build delegate ...` reste une entrée de `/build`, pas une commande séparée.

Pour les nouveaux skills, la convention recommandée est :

- `/<skill>` pour la commande principale ;
- `/<skill>-<commande>` pour les variantes ;
- éviter les alias courts non namespacés comme `/maj`, `/run` ou `/review`.

Les fichiers Python d'un skill sont du code local exécuté par BB9. Ils doivent donc venir d'une source de confiance ou être relus avant activation. Ce ne sont pas de simples prompts Markdown.

Un skill peut :

- ajouter une commande slash ;
- exposer une méthode slash Markdown par son nom d'archive ;
- intercepter une entrée utilisateur ;
- ajouter une ligne à `/context` ;
- ouvrir une capture locale temporaire ;
- orienter l'agent vers des tools existants.
- exécuter une action contrôlée par guardian/gateway si `runtime.py` déclare un protocole clair.

Toute action concrète doit rester contrôlée par le guardian et passer par une action explicitement déclarée.

## Création

Le tool natif `create_skill` aide l'agent à créer un squelette de skill utilisateur :

```text
BB9_ACTION create_skill draft <nom>
BB9_ACTION create_skill draft <nom> local
BB9_ACTION create_skill draft <nom> global
BB9_ACTION create_skill draft <nom> cli
BB9_ACTION create_skill draft <nom> runtime
BB9_ACTION create_skill draft <nom> core
```

Par défaut, il écrit dans `~/.bb9/skills/` après validation humaine.
Avec `local`, il écrit dans `.bb9/skills/` du workspace courant. Avec `global`,
il force explicitement la portée utilisateur globale.

Le template utilisateur `extension-factory` porte la méthode de création ou
d'amélioration des skills et tools BB9. Il sert à décider si le besoin relève
d'un skill utilisateur, d'un tool natif ou d'une simple section documentaire,
puis guide l'agent vers `create_skill` pour les skills ou vers l'archive
`bb9/tools/<name>/` pour les tools natifs. Il expose notamment les commandes
`/create-skill` et `/create-tool`, et doit aussi s'activer proactivement quand
une conversation révèle une méthode réutilisable.

## Vigilance

Un skill peut influencer fortement le comportement du système.

Il doit donc rester inspectable, versionné et activé selon des règles claires, surtout s'il est utilisé par un subagent, une routine planifiée ou un mode continu.

## Activation

Par défaut, un agent reçoit les skills globaux disponibles dans `~/.bb9/skills/`
et les skills locaux disponibles dans `.bb9/skills/` du workspace courant.

Quand un skill local et un skill global ont le même nom, le skill local prend le
dessus. Cela permet à un projet d'adapter `plan`, `dev` ou n'importe quel skill
partageable sans modifier la version globale.

Un agent peut désactiver certains skills avec :

```text
~/.bb9/agents/<name>/SKILLS_DISABLED.md
```

Le fichier reste en Markdown et contient une liste à puces de noms de skills :

```markdown
- my-local-skill
```

Ce choix garde la configuration lisible par l'humain tout en restant simple à parser.

Un skill `on-demand` peut déclarer des déclencheurs dans son frontmatter
`activation:` sans déclarer de commande routable. C'est utile quand une commande
projet principale doit charger un skill complémentaire sans créer de collision :

```markdown
---
name: visual-sketching
activation: /project-sketch, maquette libre, exploration visuelle
---
```

Les commandes déclarées dans `## Commandes` restent chargées pour le REPL et les
surfaces. Les déclencheurs `activation:` servent seulement à décider si le corps
du skill entre dans le prompt du tour.

Un skill local peut aussi déclarer qu'une de ses commandes doit livrer un artefact
workspace vérifiable :

```markdown
## Contrat de livraison

type: workspace-artifact
commands: /project-sketch
path: public/drafts/
link: /api/file/public/drafts/
preview: browser
```

Ce contrat est lu dynamiquement par la loop. BB9 ne doit pas coder en dur le nom
d'un skill local ou d'une commande propre à un projet. Si `preview: browser` est
présent, une tentative navigateur échouée doit être signalée dans la réponse
finale au lieu d'être présentée comme une validation visuelle.
Le champ `commands:` est optionnel, mais recommandé quand le skill expose
plusieurs commandes et que seules certaines livrent des artefacts.

## Questions à résoudre

- Faut-il rendre certains skills obligatoires ?
- Comment éviter les contradictions entre skills ?
- Comment tester qu’un skill influence bien le comportement ?
- Différence exacte entre skill, tool, routine et doc ?
