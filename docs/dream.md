# Dream

## Intention

Définir la consolidation mémoire de BB9 sans la confondre avec le cron, la
session courte ou les workflows métier.

Le dreaming lit plusieurs matières premières, les croise, puis produit de la
valeur durable :

- mémoire globale ;
- mémoire locale des projets ;
- sessions récentes ;
- documents projet utiles ;
- contrats `DREAM.md` des skills actifs ;
- contrats `DREAM.md` des tools actifs.

Le dreaming consolide, relie, corrige et propose. Il n'exécute pas d'action
métier directement.

## Frontière Avec Cron

`DREAM.md` ne porte pas de cadence.

Le déclenchement périodique appartient à `CRON.md` :

```text
~/.bb9/cron/nightly-dream/CRON.md
```

Le dreaming est lancé par une intention, une commande explicite ou un cron. Il
ne recrée pas son propre scheduler.

Résumé :

```text
CRON.md  = quand lancer
dreaming = consolider et produire de la valeur
DREAM.md = dire ce qu'une brique apporte au dreaming
```

## Archive De Cycle

Forme cible :

```text
~/.bb9/dreams/<name>/DREAM.md
```

Sections attendues :

- `Résumé` : description courte affichable dans les index ;
- `Activation` : `active` ou `paused` ;
- `Agent` : agent cible si le cycle passe par un agent ;
- `Scope` : `global`, `project` ou un périmètre plus précis ;
- `Sources` : matières premières à collecter ;
- `Memory Policy` : règles de consolidation mémoire ;
- `Output` : format attendu du rapport ;
- `Guardrails` : limites.

Exemple :

```markdown
# DREAM.md

## Résumé

Consolidation quotidienne de la mémoire globale et du projet actif.

## Activation

active

## Agent

default

## Scope

project

## Sources

- Memory globale.
- Memory du projet actif.
- Sessions récentes non consolidées.
- DREAM.md des skills et tools actifs.
- DECISIONS.md et ROADMAP.md du projet si présents.

## Memory Policy

- Garder les faits durables.
- Distinguer global et project.
- Relier les faits quand une relation utile existe.
- Remplacer les formulations périmées plutôt qu'empiler les doublons.

## Output

- Opérations mémoire SQL graph.
- Actions proposées, jamais exécutées.
- Résumé court et sourcé.

## Guardrails

- Ne pas stocker de secret.
- Ne pas transformer une préférence ponctuelle en règle durable.
- Ne pas exécuter de tool métier pendant le dreaming.
```

## Contribution De Skill Ou Tool

Un skill ou un tool peut fournir un `DREAM.md` local :

```text
~/.bb9/skills/<skill>/DREAM.md
bb9/tools/<tool>/DREAM.md
```

Ce fichier ne définit pas un cycle complet. Il dit seulement ce que cette brique
apporte au dreaming.

Sections recommandées :

- `Purpose` : valeur apportée au dreaming ;
- `Inputs` : signaux ou données que la brique peut fournir ;
- `Signals` : types de signaux nommés ;
- `Proposed Actions` : actions que le dreaming peut proposer ;
- `Output Guidance` : règles de sortie ;
- `Guardrails` : limites spécifiques.

Exemple :

```markdown
# RAG Dream Contract

## Purpose

Repérer les sources consultables qui contiennent des règles durables ou des
contradictions à revoir.

## Inputs

- Notes consultées récemment.
- Sources taguées `always`, `important`, `fresh`.

## Signals

- `rag.always_rule`
- `rag.stale_or_conflict`
- `rag.open_markdown_task`

## Proposed Actions

- `node.add` pour un fait durable synthétique.
- `edge.add` pour relier une source à une mémoire.
- `rag.review_source` comme action proposée, jamais exécutée.

## Guardrails

- Ne pas recopier tout le corpus dans la mémoire.
- Ne pas promouvoir une tâche ouverte comme fait accompli.
- Ne pas inventer de contenu absent des sources.
```

## Mémoire SQL Graph

Le dreaming travaille avec `~/.bb9/memory.db` :

- `memory_nodes` pour les faits durables ;
- `memory_edges` pour les relations typées ;
- scope `global` pour les faits transversaux ;
- scope `project` pour les faits locaux à un projet.

Format d'opérations attendu :

```json
{
  "operations": [
    {
      "op": "node.add",
      "content": "Fait durable synthétique.",
      "scope": "project",
      "kind": "decision",
      "tags": "bb9,dreaming",
      "source": "DECISIONS.md",
      "confidence": "high"
    },
    {
      "op": "edge.add",
      "source_id": 1,
      "target_id": 2,
      "relation": "supports",
      "weight": 1.0,
      "source": "dreaming"
    }
  ],
  "actions": [
    {
      "kind": "rag.review_source",
      "title": "Revoir une source contradictoire",
      "content": "Résumé de l'action proposée.",
      "source": "notes/example.md",
      "confidence": "medium",
      "status": "proposed",
      "reason": "Contradiction repérée entre mémoire et source."
    }
  ],
  "summary": "Bilan court."
}
```

Les `actions` ne sont pas appliquées automatiquement. Elles servent à conserver
des suites utiles, sourcées et auditables.

## Sessions Persistées

Le dreaming peut lire les sessions récentes depuis :

```text
~/.bb9/sessions.db
```

Les sessions ne sont pas une mémoire durable. Elles sont une matière première :
le moteur peut y repérer une décision, une préférence stable, une contradiction
ou une suite utile, puis proposer une opération mémoire explicite.

Règles :

- lire les sessions globales et celles du projet actif ;
- utiliser les résumés de compaction quand ils existent ;
- ne pas recopier une conversation entière dans la mémoire ;
- ne pas promouvoir un message isolé sans valeur durable ;
- garder une source claire, par exemple `session:<id>`.

Cette séparation évite que la conversation devienne automatiquement mémoire. Le
passage session -> mémoire reste une consolidation.

## Runner Initial

Le runner initial de BB9 sait :

- charger les archives `~/.bb9/dreams/<name>/DREAM.md` ;
- charger les contributions `DREAM.md` des skills et tools ;
- lire les sessions persistées quand un `SessionStore` lui est fourni ;
- construire un contexte de consolidation ;
- générer un prompt de consolidation ;
- appeler le provider actif quand un run explicite le demande ;
- produire un plan pending avant application ;
- parser les opérations JSON ;
- appliquer les opérations mémoire SQL graph.

Il ne sait pas encore :

- décider de sa cadence ;
- exécuter les actions proposées.

Ces responsabilités seront branchées autour du runner, pas cachées dans
`DREAM.md`.

## Commande CLI

La commande explicite est :

```text
/dream
```

Sous-commandes :

- `/dream status` : liste les archives `DREAM.md` ;
- `/dream index` : régénère l'index Markdown des dreams ;
- `/dream context [name]` : affiche les compteurs de contexte ;
- `/dream prompt [name]` : affiche le prompt complet qui serait envoyé au provider ;
- `/dream preview [name]` : appelle le provider et stocke un plan pending sans l'appliquer ;
- `/dream apply [name]` : applique le plan pending ;
- `/dream run [name]` : appelle le provider actif et applique les opérations mémoire retournées.

Sans nom, BB9 choisit le premier dream actif, puis le premier dream disponible.

`/dream run` ne déclenche aucun tool métier. Les actions retournées par le
provider restent proposées et affichables ; seules les opérations mémoire SQL
graph sont appliquées.

Le chemin contrôlé est optionnel :

```text
/dream preview nightly
/dream apply nightly
```

Le plan pending vit dans :

```text
~/.bb9/dream-pending.json
```

Ce fichier est un état runtime temporaire. Il ne remplace pas `DREAM.md` et ne
devient pas une mémoire durable.

## Planification Par Cron

Le dreaming n'a pas son propre scheduler. Pour le lancer à heure fixe, on
utilise une archive `CRON.md` classique avec une section `Command` :

```text
/dream run nightly
```

Le cron décide quand lancer. Le dream décide quoi consolider.

## Questions À Résoudre

- Où stocker les rapports de dream ?
- Faut-il archiver le rapport brut provider de chaque run ?
