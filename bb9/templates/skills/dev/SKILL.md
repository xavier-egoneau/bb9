---
activation: on-demand
name: dev
description: Exécuter un plan BB9 en respectant dépendances, parallélisation et retours de tâches.
---

# Build

## Résumé

Exécuter un plan BB9 en respectant dépendances, parallélisation et retours de tâches.

## Activation

Quand l'utilisateur demande `/build`, l'exécution d'un plan, la coordination de
tâches ou la préparation d'une délégation contrôlée.

## Portée

Template global utilisateur. Un projet peut le spécialiser avec
`.bb9/skills/dev/SKILL.md`, qui prendra le dessus dans ce workspace.

## Commandes

- `/build` : exécuter séquentiellement le plan courant `.bb9/plan.md`.
- `/build delegate` : déléguer une tâche standalone à un subagent.

`/build` lit le plan produit par `/plan`. `/build delegate` reste une primitive
explicite pour déléguer une seule tâche. Les autres usages de `/build ...` restent
une méthode Markdown.

Exemple :

```text
/build delegate id=T1 worker=default goal="Analyser le module" context="Le parent a lu la roadmap." expected="Résumé court avec preuves."
```

Format minimal d'un plan :

```markdown
- [ ] T1 Lire le contexte
  worker: default
  parallelizable: false
  paths: docs/subagents.md
  depends:
  goal: Lire le contexte.
  context: Le parent a cadré le besoin.
  expected: Résumé court.

- [ ] T2 Synthétiser
  worker: default
  depends: T1
  goal: Synthétiser.
  context: T1 est terminé.
  expected: Synthèse finale.
```

## Rôle

Tu exécutes le plan. Tu gardes la trace canonique dans la conversation, tu
lances seulement les tâches dont les dépendances sont satisfaites et tu
collectes les résultats.

La première exécution de plan est séquentielle. Les tâches marquées
`parallelizable: true` peuvent être lancées en parallèle seulement si elles ont
des `paths:` non vides et sans intersection avec les autres tâches de la vague.
Sans `paths:`, ou en cas de conflit, `/build` reste séquentiel.
Après une tâche réussie, `/build` coche sa case dans `.bb9/plan.md`.
Après une tâche exécutée, `/build` écrit sous la tâche un état court (`status`,
`summary`, et si besoin `blockers` ou `evidence`) pour permettre une reprise
simple.

Les ids comme `T1` et `T2` servent aux dépendances dans le Markdown. Dans le chat
canonique et le récap final, `/build` parle avec les titres humains des tâches et
résume naturellement ce qui est fait, ce qui bloque et le prochain pas utile.

## Exécution

Pour chaque tâche :

- vérifier les dépendances ;
- choisir le worker local ou subagent suggéré ;
- transmettre un objectif explicite et le contexte strictement nécessaire ;
- lancer les tâches parallélisables sans attendre les autres tâches indépendantes ;
- attendre les tâches qui bloquent la suite ;
- résumer l'état utile à l'utilisateur.

## TaskResult

Chaque tâche doit revenir avec :

- `task_id` ;
- `status: done | error` ;
- `summary` ;
- `changed` ;
- `observed` ;
- `blockers` ;
- `evidence` ;
- `next_suggestion`.

## Règles

- `done` exige une preuve ou une observation.
- `error` doit expliquer le blocage concret.
- Une dépendance en erreur bloque les tâches dépendantes.
- Si une tâche indique `missing expected output`, le plan est invalide. Ne pas
  relancer `/build --retry-errors` en boucle ; compléter `expected_output` ou
  régénérer le plan avec `/plan`.
- Si les chemins du plan n'existent pas dans le workspace courant, vérifier le
  workspace avant toute édition. Ne pas créer une arborescence seulement parce
  qu'un plan la mentionne.
- Aucun subagent ne reçoit une mission vague.
- Aucun droit implicite n'est ajouté par `/build`.
- Les actions concrètes restent soumises au guardian et au gateway.
- Le parent tient l'utilisateur au courant dans le chat canonique.
