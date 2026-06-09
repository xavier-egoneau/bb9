# Subagents

## Intention

Prendre en compte les subagents dès la conception sans construire trop tôt un système multi-agent complexe.

Un subagent est une unité de travail déléguée : mission bornée, contexte réduit, droits explicites et résultat synthétique.

Une délégation correcte commence avant le subagent : le parent doit produire une
tâche standalone, comparable à une user story autonome, avec objectif, contexte,
contraintes, critères de done et résultat attendu.

## Contrat

Les subagents doivent :

- avoir un nom et une responsabilité claire ;
- recevoir une intention déléguée et un contexte minimal ;
- recevoir une tâche précise et standalone ;
- déclarer leurs tools, skills et permissions autorisées ;
- passer par le guardian avant toute action sensible ;
- remonter tout `ask` guardian au parent, qui le présente à l'utilisateur ;
- retourner une observation ou synthèse exploitable par la loop principale ;
- pouvoir être désactivés ou ignorés sans casser la boucle simple.

Les subagents ne doivent pas :

- remplacer la loop principale ;
- posséder la mémoire durable ;
- contourner le gateway ;
- contourner les hooks ou le guardian ;
- lancer d'autres subagents sans règle explicite ;
- devenir une excuse pour créer une architecture prématurée.

## Position provisoire

Le projet prévoit les subagents dans les contrats dès le départ, mais l'implémentation initiale reste mono-agent.

Un subagent peut vivre dans le dossier de son agent parent :

```text
~/.bb9/agents/<agent>/subagents/<subagent>/
  IDENTITY.md
  SOUL.md
  MODEL.md
  SKILLS_DISABLED.md
  TOOLS_DISABLED.md
```

Le subagent `default` est le fallback attendu quand une tache doit etre deleguee mais qu'aucune specialisation ne correspond clairement. Il n'est pas l'agent parent bis : il sert a isoler une mission bornee dans un contexte separe.

S'il n'a pas de dossier, il n'existe pas comme subagent configuré.

S'il a un dossier mais qu'un fichier manque :

- `IDENTITY.md` hérite de l'agent parent ;
- `SOUL.md` hérite de l'agent parent ;
- `MODEL.md` hérite de l'agent parent ;
- `SKILLS_DISABLED.md` s'ajoute aux skills désactivés du parent ;
- `TOOLS_DISABLED.md` s'ajoute aux tools désactivés du parent.

Cet héritage garde les subagents légers et permet de spécialiser seulement ce qui change.

## Index

Le runtime genere un index court dans :

```text
~/.bb9/agents/<agent>/subagents/INDEX.md
```

Cet index liste les subagents disponibles et leur usage principal, extrait de `IDENTITY.md`. Il est injecte dans le contexte du parent pour eviter un choix au hasard.

La convention minimale est :

- `default` : worker generique quand aucune specialisation ne colle mieux ;
- `goal` : worker utilise par `/goal` pour faire avancer une iteration sans valider le succes final ;
- un subagent specialise doit decrire `Quand l'utiliser` dans `IDENTITY.md`.

`MODEL.md` permet d'optimiser un subagent avec un modele plus leger tout en gardant le provider et l'authentification actifs. Exemple pour `subagents/goal/MODEL.md` :

```md
# Model

Model : gpt-5-mini
ReasoningEffort : low
```

La première forme acceptable est une délégation interne très simple :

```text
intention principale -> décision -> tâche déléguée -> résultat borné -> reprise par la loop principale
```

## Task

Une tâche déléguée doit contenir :

```text
Task
- id
- title
- goal
- context
- inputs
- expected_output
- done_criteria
- dependencies
- parallelizable
- suggested_worker
- permission_profile
- tool_scope
- max_iterations
```

Le parent ne délègue pas une tâche si le contexte fourni ne permet pas au
subagent d'avancer sans deviner le problème global.

## TaskResult

Le subagent retourne :

```text
TaskResult
- task_id
- status: done | error
- summary
- changed
- observed
- blockers
- evidence
- next_suggestion
```

Le parent relaie dans le chat canonique les lancements, fins, erreurs et
conséquences sur le plan. Le subagent ne parle pas directement à l'utilisateur.
Si une action du subagent exige une validation, elle remonte au parent : la
surface utilisateur affiche l'ask, puis la délégation reprend avec l'observation
`allow` ou `deny`.

## Plan Et Dev

`/plan` et `/build` sont les skills qui décident quand et comment utiliser les
subagents.

`/plan` découpe la demande en tâches, dépendances et tâches parallélisables.

`/build` exécute le plan : il attend les dépendances, lance les tâches
parallélisables sans bloquer la suite, collecte les retours et met à jour l'état
du travail.

Dans le chat web, les lancements de subagents doivent rester visibles comme des
branches de travail : worker utilisé, tâche confiée, statut en cours/terminé/en
erreur. Le subagent ne parle toujours pas directement à l'utilisateur ; la
surface affiche seulement l'orchestration et l'état public de sa tâche.
Une demande d'autorisation issue d'une branche subagent reste donc une demande
utilisateur normale : elle porte la tâche et le worker concernés, suspend le
build, puis reprend la même tâche après décision.

Le runtime futur de délégation doit rester un contrat court :

```text
delegate(task, subagent) -> TaskResult
```

Première forme runtime :

```python
delegate(task, subagent, parent_context, runner) -> TaskResult
```

`runner` est injecté pour garder la délégation découplée du CLI, du provider et
d'un éventuel worker futur.

Le runtime expose aussi un tool natif `delegate` pour permettre au parent de
demander une délégation bornée avec `BB9_ACTION delegate run ...`. Ce tool reste
une façade fine au-dessus du contrat `delegate(task, subagent, parent_context,
runner)` : il ne crée pas de scheduler, ne contourne pas le guardian et ne donne
pas au subagent une voix directe dans le chat utilisateur.

Par défaut, une délégation utilise `tool_scope=dev` : le subagent reçoit
uniquement les tools de développement (`shell`, `files`, `browser`, `web`,
`vision`). Il ne reçoit pas `delegate`, `secret`, `tasks`, `caldav` ou les tools
de configuration. Les trusted roots du parent ne sont pas hérités ; un subagent
travaille dans le workspace actif et ne doit pas sortir du dossier courant.

Le runtime de délégation :

- valide que la tâche contient au minimum `id`, `goal`, `context` et `expected_output` ;
- construit un contexte réduit pour le subagent ;
- remplace la session par une session courte `delegation:<task-id>` ;
- vide l'index des subagents pour éviter la délégation récursive libre ;
- plafonne le profil de permission demandé par la tâche au profil du parent ;
- filtre les tools selon le scope de la tâche, `dev` par défaut ;
- retire les trusted roots hérités pour garder le worker dans le workspace actif ;
- convertit l'observation du runner en `TaskResult`.

Il ne fait pas encore :

- orchestration de plan ;
- parallélisme ;
- retry ;
- file de jobs ;
- écriture d'historique complet.

Ces responsabilités restent au-dessus du contrat minimal, notamment dans un
futur `/build`.

## Questions à résoudre

- Comment tracer une délégation sans rendre la trace illisible ?
- Quels subagents réels justifient une première implémentation ?
