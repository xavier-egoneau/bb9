# Plan Et Dev

## Intention

Définir la méthode BB9 pour découper une demande complexe, détecter ce qui est
parallélisable et exécuter les tâches sans transformer les subagents en système
autonome caché.

`/plan` et `/build` sont d'abord des skills de méthode :

- `/plan` transforme une demande en plan structuré ;
- `/build` exécute ce plan, séquentiellement ou en parallèle quand c'est sûr.

BB9 les installe comme templates de skills utilisateur dans `~/.bb9/skills/`
si absents. Ils restent donc modifiables et partageables par l'utilisateur.
Un projet peut les spécialiser localement avec `.bb9/skills/plan/` ou
`.bb9/skills/dev/`, qui prennent alors le dessus dans ce workspace.

Le runtime de délégation vient ensuite. Il doit rester petit.

`/plan` et `/build` partagent un état de plan courant. La première
implémentation le persiste dans :

```text
.bb9/plan.md
```

`/plan` écrit cet état et l'écrase à chaque nouveau plan. `/build` le lit sans
argument et exécute ses tâches séquentiellement. `/build delegate` reste une
primitive explicite pour une tâche unique.

Dans le chat web, une demande naturelle clairement multi-étapes peut déclencher
automatiquement le mode plan si aucun plan courant n'existe : feature complète,
refactor, migration, architecture, workflow, plusieurs fichiers, correction plus
tests, longue tâche, proposition de nouveaux composants pour un design system,
demande explicite de plan, ou continuation du type "je veux tout ça" après une
proposition structurée. Ce déclenchement écrit seulement `.bb9/plan.md` et ne
lance jamais `/build` sans demande explicite de l'utilisateur.

Dans le chat web, ce fichier n'est pas présenté comme l'objet utilisateur. Le
channel rend le plan courant comme une carte repliable au-dessus du composer,
avec un titre, le nombre de tâches terminées, les tâches en lecture seule et une
raison courte pour les tâches en erreur. La carte peut aussi vider le plan sans
être ouverte, via une action explicite dans son en-tête. Le fichier reste un
détail de reprise pour le runtime initial.

La carte du chat web est liée au workspace d'exécution courant. Quand l'utilisateur
change de projet, le serveur web change de workspace et la carte doit être
rechargée depuis le `.bb9/plan.md` de ce nouveau projet, ou masquée si ce plan
n'existe pas. Un payload de plan portant un autre projet ne doit pas réafficher un
ancien plan après le switch.

Format minimal lu par `/build` :

```markdown
# BB9 Plan

Objective: ...

## Tasks

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
- paths
- suggested_worker
- permission_profile
- max_iterations
```

Règles :

- une tâche sans contexte suffisant n'est pas délégable ;
- une tâche avec dépendance non satisfaite n'est pas lançable ;
- `parallelizable` doit être explicite ;
- `paths` déclare les zones touchées et rend le parallélisme vérifiable ;
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

## Skill `/build`

Le skill `/build` sert à exécuter le plan.

Il lit le plan, puis :

- lance une tâche seulement si ses dépendances sont satisfaites ;
- exécute séquentiellement les tâches dépendantes ;
- lance une tâche parallélisable sans attendre les autres tâches indépendantes ;
- continue sur la tâche suivante quand c'est possible ;
- collecte les résultats des tâches en cours ;
- marque les tâches `done` ou `error` ;
- arrête ou demande arbitrage si une dépendance échoue.

`/build` peut lancer une vague de tâches en parallèle seulement si elles sont
prêtes, marquées `parallelizable: true`, avec `paths:` non vide et sans
intersection entre elles. Sans `paths:`, ou en cas de conflit de paths, il reste
séquentiel. Un chemin parent et un chemin enfant, comme `docs` et
`docs/skills.md`, comptent comme un conflit. Après une tâche réussie, `/build`
coche la case correspondante dans
`.bb9/plan.md`. Il écrit aussi un état court sous la tâche exécutée (`status`,
`summary`, et si besoin `blockers` ou `evidence`) pour rendre la reprise lisible
sans journal externe.

Une tâche déjà marquée `status: error` n'est pas relancée par un `/build`
ordinaire. Cet état signifie qu'il faut d'abord regarder l'erreur, nettoyer le
plan ou demander explicitement un retry avec `/build --retry-errors`. Cela évite
de réinjecter dans un nouveau run les anciens `summary`, `blockers` ou
`evidence` qui ont été écrits pour diagnostiquer l'échec précédent.

Les états issus uniquement d'une dépendance non prête (`dependency:*` ou
"dependencies could not be resolved") sont des blocages recalculables, pas des
erreurs directes. `/build` peut les reconsidérer dès que les dépendances
changent.

Les ids (`T1`, `T2`, etc.) restent des ancres internes pour `depends:` et la
machine. La sortie conversationnelle de `/build` doit parler en titres humains :
`Lire le contexte`, `Adapter la documentation`, `Synthétiser`. Le récap final
est une synthèse en langage naturel de ce qui est terminé, bloqué, et du prochain
pas utile.

`/build` ne donne pas de droits implicites. Chaque action reste soumise au profil,
au guardian et au gateway.

Le runtime de `/build` conserve un résultat structuré distinct de sa sortie
texte. Les lignes `plan...`, `task...` et `sum...` sont des marqueurs live ou
diagnostiques, pas la réponse canonique à l'utilisateur. Une surface peut donc
rendre :

- une trace courte pendant l'exécution ;
- une synthèse humaine finale ;
- un artefact diagnostique contenant la sortie brute et les traces de
  subagents utiles au debug.

Dans le chat web, la réponse finale de `/build` doit rester une synthèse courte
de ce qui est terminé, en erreur, bloqué par dépendance, et du prochain pas
utile. La sortie brute est conservée comme artefact caché par défaut afin
d'aider au diagnostic sans noyer le chat.

Si un subagent lancé par `/build` déclenche un `ask` guardian dans le chat web,
le build est suspendu sans marquer la tâche en erreur dans le plan. La validation
affichée porte le contexte de reprise de la tâche et le worker concerné. Une
autorisation reprend le subagent avec l'observation de l'action exécutée, puis
continue le build. Un refus reprend le subagent avec une observation de refus,
pour lui permettre de chercher une alternative ou de retourner un blocage clair.
Si le même subagent redemande une validation plus tard dans la tâche, la même
chaîne de reprise s'applique : ask utilisateur, décision, observation, reprise.

Dans le chat web, `/build` garde le profil `safe` séquentiel afin d'éviter deux
demandes de validation concurrentes provenant de subagents parallèles. En
`limited` ou `power`, les tâches éligibles restent parallélisées : le profil
réduit naturellement le nombre de confirmations nécessaires dans le workspace.

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
- un `status: done` explicite prévaut sur des réserves textuelles comme
  "runtime non vérifié" ou "aucun accès hors workspace requis" ;
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
Lire le contexte lancée sur subagent research.
Adapter la documentation exécutée localement.
Lire le contexte terminée: résumé court.
Synthétiser bloquée: la tâche Lire le contexte n'est pas terminée.
```

La trace doit rester utile, pas bavarde. Les ids de tâche peuvent exister dans
le Markdown, mais ils ne doivent pas être la langue principale du chat.

Dans le chat web, les marqueurs structurés produits par les skills (`plan...`,
`parallel...`, `task...`, `blocker...`, `blk...`) sont adaptés en événements
`process` live. Cette adaptation sert uniquement la progression visible et ne
remplace pas la sortie finale du skill ni l'état persistant dans `.bb9/plan.md`.
Quand `/build` lance un worker, la trace expose un événement typé `subagent`
avec le worker, la tâche et le statut (`running`, `done`, `error`). La surface
peut donc afficher une branche par subagent, avec un indicateur actif pendant
son travail. Ces événements sont aussi attachés au message comme trace de
décision cachée afin de rester visibles dans l'historique après rechargement du
chat.
La surface live ne doit pas perdre un subagent `running` simplement parce que
d'autres événements arrivent après lui : plusieurs workers parallèles peuvent
donc afficher plusieurs indicateurs actifs en même temps.

## Délégation Runtime

La forme runtime future doit rester courte :

```text
delegate(task, subagent) -> TaskResult
```

La première implémentation runtime garde un runner injecté :

```python
delegate(task, subagent, parent_context, runner) -> TaskResult
```

Elle pose le contrat d'une tâche standalone et d'un résultat structuré. Le skill
`/build` s'appuie dessus pour exécuter un plan complet et lancer des tâches en
parallèle seulement quand le plan marque les tâches comme parallélisables et que
leurs chemins ne se chevauchent pas.

Garde-fous :

- pas de délégation récursive libre ;
- pas de mémoire durable propre au subagent ;
- pas de droits supérieurs au parent ;
- pas d'écriture directe hors guardian/gateway ;
- pas d'accès à toute la session si le contexte réduit suffit.

## Frontières

`/plan` et `/build` sont des skills parce qu'ils décrivent une méthode de travail
que l'utilisateur pourra adapter.

Le runtime de délégation est une brique core parce qu'il applique un contrat
court et contrôlé entre parent et subagent.

Un dashboard futur, le chat web ou toute autre surface peuvent afficher le plan
et les tâches, mais ils ne doivent pas devenir seuls propriétaires de l'état
exécutable.
