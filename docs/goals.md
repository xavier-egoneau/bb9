# Goals

## Intention

Définir un objectif persistant qui modifie le modèle d'exécution : BB9 ne répond plus une seule fois, il boucle jusqu'à succès vérifié, blocage, pause, annulation ou limite.

Un goal n'est pas une note. C'est un état d'orchestration.

## Contrat

Le goal doit :

- vivre dans le dossier user ;
- contenir un objectif clair, des conditions de succès, des contraintes et des vérifications ;
- enregistrer chaque itération ;
- utiliser la loop existante pour les actions ;
- passer par le guardian avant tout tool ;
- vérifier concrètement avant de déclarer le succès ;
- rester borné par un nombre maximal d'itérations.

Le goal ne doit pas :

- contourner la loop, le gateway ou le guardian ;
- déclarer un succès sur une simple phrase du modèle ;
- devenir un moteur de workflow généraliste ;
- stocker des secrets ;
- écraser la session ou la memory durable.

## Stockage

La première persistance est un JSON local :

```text
~/.bb9/goals/active.json
```

Ce fichier contient le `GoalState` courant et son historique d'itérations.

## Commandes

```text
/goal <objectif>
/goal status
/goal pause
/goal resume
/goal cancel
/goal clear
```

Créer un goal actif lance la boucle immédiatement dans le REPL.

## Boucle

La boucle suit ce cycle :

```text
goal -> worker intention -> run_once -> observations -> verification -> evaluator -> update goal
```

Le worker agit via le kernel, la loop, les hooks, le guardian, le gateway et les tools. L'évaluateur est séparé : il lit les conditions de succès et les preuves de vérification.

Dans le REPL, le worker de `/goal` utilise le subagent `goal` de l'agent actif s'il existe :

```text
~/.bb9/agents/<agent>/subagents/goal/
```

Si ce subagent n'existe pas, BB9 retombe sur `subagents/default/`, puis sur l'agent courant. Cette convention permet de configurer l'identite et les restrictions du worker sans transformer l'evaluateur critique en subagent libre.

Le but principal de ce subagent est l'optimisation : son `MODEL.md` peut pointer vers un modele plus leger que l'agent principal, tout en reutilisant le provider et l'authentification actifs.

## Évaluateur

L'évaluateur retourne une décision structurée :

```text
continue
stop_success
stop_blocked
ask_user
stop_limit
```

Un goal est atteint seulement si toutes les vérifications concrètes configurées passent. Si la vérification est absente ou impossible, le goal n'est pas atteint.

## Garde-fous

- `maxIterations` vaut 20 par défaut.
- Trois itérations sans progression bloquent le goal.
- Trois échecs critiques consécutifs de vérification bloquent le goal.
- Une interruption utilisateur met le goal en pause.

## Limites actuelles

La première version extrait des commandes de vérification simples depuis l'objectif : `npm test`, `npm run build`, `pytest`, `python -m unittest`, `make test`, `cargo test`, `go test`.

Si aucune vérification concrète n'est détectée, le goal est mis en pause avec demande de clarification plutôt que déclaré réussi.
