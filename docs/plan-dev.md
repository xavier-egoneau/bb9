# Plan Et Dev

## Intention

Définir la méthode BB9 pour découper une demande complexe, détecter ce qui est
parallélisable et exécuter les tâches sans transformer les subagents en système
autonome caché.

`/plan` et `/dev` sont d'abord des skills de méthode :

- `/plan` transforme une demande en plan structuré ;
- `/dev` exécute ce plan, séquentiellement ou en parallèle quand c'est sûr.

BB9 les installe comme templates de skills utilisateur dans `~/.bb9/skills/`
si absents. Ils restent donc modifiables et partageables par l'utilisateur.
Un projet peut les spécialiser localement avec `.bb9/skills/plan/` ou
`.bb9/skills/dev/`, qui prennent alors le dessus dans ce workspace.

Le runtime de délégation vient ensuite. Il doit rester petit.

## Principe

Un subagent ne reçoit pas une mission vague. Le parent lui mâche la tâche comme
une user story autonome.

La tâche déléguée doit être standalone :

- objectif explicite ;
- contexte suffisant ;
- dépendances déjà satisfaites ;
- fichiers, zones ou données concernées ;
- contraintes ;
- droits accordés ;
- résultat attendu ;
- critères de done ;
- format de retour.

Le parent garde la responsabilité du plan, de la coordination, de la trace dans
le chat canonique et de la synthèse finale.

## Plan

Un `Plan` est une liste de tâches avec leurs dépendances.

Structure cible :

```text
Plan
- objective
- assumptions
- tasks
- risks
- verification
```

Chaque tâche contient :

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
- max_iterations
```

Règles :

- une tâche sans contexte suffisant n'est pas délégable ;
- une tâche avec dépendance non satisfaite n'est pas lançable ;
- `parallelizable` doit être explicite ;
- une tâche parallélisable ne doit pas modifier la même zone qu'une autre tâche
  en cours sans verrou ou règle claire ;
- le plan doit pouvoir être relu par un humain.

## Skill `/plan`

Le skill `/plan` sert à produire un plan exploitable.

Il doit :

- découper la demande en tâches ;
- identifier les dépendances entre tâches ;
- dire quelles tâches sont parallélisables ;
- préciser le contexte nécessaire à chaque tâche ;
- proposer le worker ou subagent le plus adapté ;
- définir les critères de done ;
- signaler les inconnues bloquantes.

Il ne doit pas :

- lancer de subagent ;
- exécuter d'action métier ;
- déclarer une tâche finie ;
- cacher une hypothèse importante.

## Skill `/dev`

Le skill `/dev` sert à exécuter le plan.

Il lit le plan, puis :

- lance une tâche seulement si ses dépendances sont satisfaites ;
- exécute séquentiellement les tâches dépendantes ;
- lance une tâche parallélisable sans attendre les autres tâches indépendantes ;
- continue sur la tâche suivante quand c'est possible ;
- collecte les résultats des tâches en cours ;
- marque les tâches `done` ou `error` ;
- arrête ou demande arbitrage si une dépendance échoue.

`/dev` ne donne pas de droits implicites. Chaque action reste soumise au profil,
au guardian et au gateway.

## TaskResult

Un subagent retourne un résultat court et structuré.

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

Règles :

- `done` exige des preuves ou observations ;
- `error` doit expliquer le blocage et ce qui manque ;
- le subagent ne parle pas directement dans le chat canonique ;
- le parent relaie l'état utile à l'utilisateur.

## Trace Canonique

Le chat canonique reste tenu par le parent.

Le parent annonce :

- tâche lancée ;
- subagent ou worker choisi ;
- tâche terminée ;
- erreur ou blocage ;
- conséquence sur le plan.

Exemple :

```text
Task A lancée sur subagent research.
Task B exécutée localement.
Task A terminée: résumé court.
Task C bloquée: dépendance A incomplète.
```

La trace doit rester utile, pas bavarde.

## Délégation Runtime

La forme runtime future doit rester courte :

```text
delegate(task, subagent) -> TaskResult
```

Garde-fous :

- pas de délégation récursive libre ;
- pas de mémoire durable propre au subagent ;
- pas de droits supérieurs au parent ;
- pas d'écriture directe hors guardian/gateway ;
- pas d'accès à toute la session si le contexte réduit suffit.

## Frontières

`/plan` et `/dev` sont des skills parce qu'ils décrivent une méthode de travail
que l'utilisateur pourra adapter.

Le runtime de délégation est une brique core parce qu'il applique un contrat
court et contrôlé entre parent et subagent.

Un dashboard futur peut afficher le plan et les tâches, mais il ne doit pas en
devenir la source de vérité.
