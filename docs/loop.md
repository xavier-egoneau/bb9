# Loop

## Intention

Définir la boucle agentique minimale qui transforme une intention en résultat observable.

La loop orchestre le cycle : lire le contexte, appeler le kernel, faire exécuter les actions par le gateway, intégrer les observations, tracer, puis décider si une nouvelle itération est nécessaire.

## Contrat

La loop doit :

- garder un cycle explicite et lisible ;
- limiter le nombre d'itérations ;
- distinguer pensée, décision, action et observation ;
- conserver des points d'arrêt avant et après action ;
- arrêter proprement en cas de blocage, succès ou risque ;
- transmettre les événements utiles à la trace sans bruit excessif.

La loop ne doit pas :

- devenir un workflow engine prématuré ;
- cacher les erreurs du gateway ;
- boucler indéfiniment ;
- mélanger orchestration et implémentation des actions ;
- dépendre d'un canal d'entrée spécifique.

## Chemin d'exécution

La loop doit faire passer toute action par les hooks et le guardian avant le gateway.

```text
intention -> kernel -> décision -> pre-action hook -> guardian -> gateway -> tool -> post-action hook -> observation
```

Si le guardian bloque ou demande confirmation, la loop s'arrête ou attend l'utilisateur. Elle ne cherche pas un autre chemin vers le tool.

Le guardian est donc avant exécution. Le post-action hook intervient après le tool pour sécuriser l'observation, pas pour autoriser rétroactivement l'action.

## Provider

Position provisoire : le kernel peut appeler un provider abstrait pour produire une décision.

La loop reste responsable du cycle : elle transmet le contexte au kernel, reçoit la décision, applique les contrôles et intègre les observations.

Contrainte : le provider reste derrière une interface abstraite et ne peut pas appeler de tool directement.

## Budget de tools

Le budget de tools est un garde-fou anti-boucle, pas un objectif utilisateur.

Il dépend du profil de permission :

- `safe` : exploration prudente ;
- `limited` : exploration confortable ;
- `power` : exploration large.

Quand le budget est atteint, la loop demande au provider de produire la meilleure réponse possible avec les observations disponibles.
Le provider ne doit pas exposer cette limite interne comme une excuse utilisateur.

## Subagents

La loop principale peut prévoir une délégation future vers un subagent, mais elle reste responsable de l'orchestration globale.

Une délégation doit être bornée :

- intention déléguée explicite ;
- contexte réduit ;
- tools et permissions déclarés ;
- nombre d'itérations limité ;
- résultat retourné à la loop principale.

La phase initiale peut simuler cette forme sans créer plusieurs agents réels.

## Goals

Un goal ajoute une boucle d'orchestration au-dessus de `run_once`.

Chaque itération appelle la loop existante pour produire des actions et observations, puis lance une vérification concrète et un évaluateur séparé. La loop agentique de base reste inchangée : le goal ne contourne pas les hooks, le guardian ou le gateway.

## Questions à résoudre

- Quelle est la forme minimale de l'état de boucle ?
- Combien d'itérations autoriser par défaut ?
- Où stocker les observations intermédiaires ?
- Comment représenter un arrêt réussi, bloqué ou en attente utilisateur ?
- La loop est-elle synchrone au départ ?
- Comment éviter que la trace devienne une mémoire poubelle ?
