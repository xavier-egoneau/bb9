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

`/goal` n'est pas un agent et ne crée pas d'identité `goal`. C'est une commande
d'orchestration longue attachée à l'agent courant.

Pour exécuter une itération, BB9 utilise le worker `dev` s'il existe comme
subagent configurable de l'agent courant. Sinon, BB9 crée un worker `dev`
éphémère depuis le template de travail générique. Cette convention permet de
configurer un worker d'exécution sans transformer le goal lui-même en subagent.

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
