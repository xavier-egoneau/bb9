# Skills

## Intention

Définir les extensions utilisateur partageables de BB9.

Un skill est une archive Markdown autonome, générique et copiable d'un BB9 à un autre. Il peut ajouter une capacité, une méthode ou un comportement attendu. Il peut aussi avoir des fichiers backend si le besoin apparaît.

La différence principale avec un tool est son lieu et son statut :

```text
bb9/tools/           -> capacités natives livrées avec BB9
~/.bb9/skills/   -> extensions utilisateur
```

## Contrat

Les skills doivent :

- vivre dans `~/.bb9/skills/<name>/SKILL.md` ;
- décrire quand ils s’activent ;
- modifier le comportement de l’agent de façon lisible ;
- rester en Markdown autant que possible ;
- être portables ;
- éviter les chemins locaux en dur ;
- pouvoir être copiés dans le dossier skills d'un autre BB9.

Les skills ne doivent pas :

- remplacer le kernel ;
- contenir de secrets ;
- devenir une collection de prompts flous ;
- devenir un canal dormant d'instructions non revues ;
- exécuter du code local non relu via `cli.py`.

## Frontière avec tools

Un skill et un tool peuvent tous les deux porter une capacité ou un comportement attendu.

La frontière pratique est :

- un tool est livré dans l'archive BB9 ;
- un skill appartient à l'utilisateur et vit dans `~/.bb9/skills/`.

Les tools natifs peuvent donc porter leur méthode d'usage dans `TOOL.md`. Les skills servent à enrichir ou personnaliser BB9 localement.

## Archive

Structure minimale :

```text
~/.bb9/skills/<name>/SKILL.md
```

Structure avec extension REPL :

```text
~/.bb9/skills/<name>/SKILL.md
~/.bb9/skills/<name>/cli.py
```

`SKILL.md` porte le comportement attendu. `cli.py` est optionnel et peut exposer `register(cli)` pour ajouter une commande ou un comportement REPL.

Le CLI charge les extensions de skills avec le même principe que les extensions de tools : le noyau reste hôte générique, le skill enregistre ce dont il a besoin.

Un `cli.py` de skill est du code local exécuté au démarrage du REPL. Il doit donc venir d'une source de confiance ou être relu avant activation. Ce n'est pas un simple prompt Markdown.

Un skill peut :

- ajouter une commande slash ;
- intercepter une entrée utilisateur ;
- ajouter une ligne à `/context` ;
- ouvrir une capture locale temporaire ;
- orienter l'agent vers des tools existants.

Toute action concrète doit rester contrôlée par le guardian et passer par un tool ou une action explicitement déclarée.

En phase 1, un skill utilisateur n'a pas de runtime d'action autonome chargé par le gateway. S'il veut agir, il oriente l'agent vers un tool existant ou ajoute une commande REPL locale explicite. Un support `runtime.py` pour skills reste une décision future à poser seulement sur besoin réel.

## Création

Le tool natif `create_skill` aide l'agent à créer un squelette de skill utilisateur :

```text
BB9_ACTION create_skill draft <nom>
BB9_ACTION create_skill draft <nom> cli
```

Il écrit dans `~/.bb9/skills/` après validation humaine.

## Vigilance

Un skill peut influencer fortement le comportement du système.

Il doit donc rester inspectable, versionné et activé selon des règles claires, surtout s'il est utilisé par un subagent, une routine planifiée ou un mode continu.

## Activation

Par défaut, un agent reçoit tous les skills disponibles dans `~/.bb9/skills/`.

Un agent peut désactiver certains skills avec :

```text
~/.bb9/agents/<name>/SKILLS_DISABLED.md
```

Le fichier reste en Markdown et contient une liste à puces de noms de skills :

```markdown
- my-local-skill
```

Ce choix garde la configuration lisible par l'humain tout en restant simple à parser.

## Questions à résoudre

- Faut-il rendre certains skills obligatoires ?
- Comment éviter les contradictions entre skills ?
- Comment tester qu’un skill influence bien le comportement ?
- Différence exacte entre skill, tool, routine et doc ?
