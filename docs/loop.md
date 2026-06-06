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

Quand un tool échoue pour une raison structurelle dans le tour, par exemple `browser`
sans Playwright disponible, la loop ne doit pas relancer le même tool en boucle. Elle
marque le tool indisponible pour ce tour et force une réponse finale avec les
observations déjà obtenues.

Quand une action échoue mais peut être réparée par une autre action, la loop bloque
le retry exact sans fermer immédiatement le tour. Exemple : `browser` peut échouer
sur `http://127.0.0.1:3000` avec `ERR_EMPTY_RESPONSE` parce qu'un serveur local est
muet ; l'agent doit alors pouvoir démarrer un serveur responsive avec `shell` et
utiliser l'URL retournée, au lieu de réessayer exactement la même navigation.

Pour les commandes de skills qui déclarent un `Contrat de livraison` de type
`workspace-artifact`, la loop peut refuser une réponse finale qui ne rattache pas
explicitement le résultat aux fichiers produits. Une tentative `browser` échouée
ne vaut pas validation visuelle quand le contrat demande `preview: browser` : la
réponse finale doit soit corriger la preview, soit signaler clairement l'échec
avec les liens fichiers disponibles.

Un channel peut tourner dans une boucle asyncio. Les tools synchrones qui dépendent
d'une librairie refusant cette boucle doivent isoler leur exécution ou dégrader
clairement. `browser` exécute toujours Playwright dans un thread dédié, ce qui
évite de dépendre d'une détection fragile de la surface appelante.

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
