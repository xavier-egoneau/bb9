# Archives Markdown

## But

Définir la forme commune des briques BB9 pilotées par Markdown.

BB9 ne cherche pas à faire moins qu'un assistant local complet. Il cherche à
placer la complexité durable dans des fichiers lisibles, copiables et
modifiables, puis à garder le Python comme runtime générique.

## Principe

Une archive Markdown est un dossier nommé qui décrit une capacité, une identité,
un comportement, une routine ou une politique.

Le Markdown porte :

- l'intention ;
- la configuration non sensible ;
- les comportements attendus ;
- les politiques ;
- les workflows ;
- les protocoles d'usage ;
- les règles d'activation, d'héritage et de désactivation.

Le Python porte seulement :

- la découverte ;
- le chargement ;
- la validation ;
- l'indexation ;
- les runners génériques ;
- les adapters d'exécution ;
- les frontières de sécurité.

Si une brique demande beaucoup de Python spécifique avant d'être lisible en
Markdown, elle doit être redécoupée.

## Forme générale

```text
<root>/<name>/
  <KIND>.md
  *.md
  runtime.py      # optionnel, backend d'action local
  cli.py          # optionnel, extension REPL locale
```

`<KIND>.md` est le fichier principal de l'archive. Il doit permettre de
comprendre la brique sans ouvrir son code.

Les fichiers Python optionnels ne remplacent jamais le contrat Markdown. Ils
implémentent seulement une action concrète, une commande locale ou un adapter.

## Types d'archives

### Agent

Racine utilisateur :

```text
~/.bb9/agents/<agent>/
  IDENTITY.md
  SOUL.md
  MODEL.md
  SKILLS_DISABLED.md
  TOOLS_DISABLED.md
  subagents/<subagent>/
```

Un agent décrit une identité de travail durable. `IDENTITY.md` et `SOUL.md`
sont du contexte actif, pas de la documentation secondaire.

### Subagent

Racine utilisateur :

```text
~/.bb9/agents/<agent>/subagents/<subagent>/
  IDENTITY.md
  SOUL.md
  MODEL.md
  SKILLS_DISABLED.md
  TOOLS_DISABLED.md
```

Un subagent hérite du parent et ne redéfinit que ce qui change. Il sert à une
délégation bornée avec contexte, tools et permissions séparés.

### Skill

Racine utilisateur :

```text
~/.bb9/skills/<skill>/
  SKILL.md
  DREAM.md       # optionnel, contribution au dreaming
  cli.py          # optionnel
  runtime.py      # décision future
```

Un skill ajoute une méthode, une posture, une commande ou un comportement
réutilisable. Il appartient à l'utilisateur et doit rester copiable entre BB9.

### Tool

Racine repo :

```text
bb9/tools/<tool>/
  TOOL.md
  DREAM.md       # optionnel, contribution au dreaming
  runtime.py      # optionnel ou requis si le tool agit
  cli.py          # optionnel
```

Un tool est une capacité native livrée avec BB9. `TOOL.md` décrit quand
l'utiliser, son protocole et ses garde-fous. Toute action concrète passe par le
guardian.

### Cron

Forme cible :

```text
~/.bb9/cron/<routine>/
  CRON.md
```

Une routine cron décrit une intention récurrente ou différée, son agent cible,
son calendrier, ses limites, ses conditions d'arrêt et sa politique de
notification. Le scheduler doit rester un runner générique qui lit ces archives.
Le mode `once` représente une tâche planifiée unitaire ; le mode `recurring`
représente une routine qui reste active après exécution. L'état calculé des runs
ne doit pas être réécrit dans `CRON.md`.

### Dream

Forme cible :

```text
~/.bb9/dreams/<dream>/
  DREAM.md
```

Un dream décrit une consolidation périodique : signaux à lire, mémoire à
promouvoir, bruit à ignorer, actions proposées et garde-fous. Le dreaming ne
doit pas connaître en dur les métiers des skills.

Les `DREAM.md` présents dans les skills ou tools ne sont pas des cycles
complets. Ce sont des contrats de contribution : ils décrivent la valeur que la
brique apporte au moteur dreaming.

La cadence du dreaming ne vit pas dans `DREAM.md`. Elle est déclarée par une
archive `CRON.md` qui lance explicitement le dreaming.

### Workflow

Forme cible :

```text
~/.bb9/workflows/<workflow>/
  WORKFLOW.md
```

Un workflow décrit une séquence de travail réutilisable sans l'enfermer dans du
code. Il peut orchestrer des tools, skills, subagents ou vérifications, mais le
runner garde les permissions et l'exécution sous contrôle.

## Découverte

Une archive est découvrable si :

- son dossier a un nom stable ;
- son fichier principal existe ;
- son nom ne contient que lettres, chiffres, tirets ou underscores ;
- elle n'est pas désactivée par l'agent, le profil ou la politique active.

Les index générés doivent résumer les archives disponibles sans injecter tout
leur contenu dans chaque tour.

## Activation

Par défaut :

- les tools natifs sont disponibles sauf désactivation par agent ;
- les skills utilisateur sont disponibles sauf désactivation par agent ;
- les subagents sont disponibles pour leur agent parent ;
- les cron et dreams ne s'exécutent que s'ils sont explicitement activés ;
- les workflows sont appelés explicitement ou par skill.

Les désactivations doivent rester en Markdown, inspectables et modifiables.

## Frontmatter

Le frontmatter YAML minimal peut être accepté pour les métadonnées courtes :

```markdown
---
name: example
activation: on-demand
description: Résumé court.
---
```

Le corps Markdown reste la source principale de compréhension. Le frontmatter ne
doit pas devenir un langage de programmation caché.

## Secrets

Aucun secret brut ne doit vivre dans une archive Markdown.

Une archive référence un secret par nom, par exemple :

```text
secret:OPENAI_API_KEY
```

Le runtime résout ce secret localement au dernier moment, hors contexte
provider, et le guardian garde le contrôle des actions sensibles.

## Invariant

Le kernel ne doit pas devenir propriétaire du contenu des archives. Il reçoit un
contexte préparé et des contrats courts. Les chargeurs, runners et adapters
peuvent évoluer, mais les décisions durables restent lisibles en Markdown.
