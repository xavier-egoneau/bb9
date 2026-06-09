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

Si le guardian demande confirmation, la loop produit une observation
`approval_pending` et laisse le channel gérer l'attente utilisateur. Après
autorisation, la continuation réinjecte l'observation de l'action approuvée.
Après refus, la continuation réinjecte une observation de refus : l'agent peut
alors chercher une autre voie ou conclure avec un blocage explicite. Si le
guardian bloque (`block`), la loop n'essaie pas de contourner le tool interdit.
Elle propage alors `block_category` dans l'événement guardian, le process public
et l'observation transmise au provider, afin que le blocage ne soit pas confondu
avec une simple demande de droits.
Si le tour arrive en réponse finale après un ou plusieurs vrais blocks, la loop
peut produire elle-même une réponse de secours avec la catégorie et la raison du
dernier block, pour éviter une explication utilisateur trop vague.

Le guardian est donc avant exécution. Le post-action hook intervient après le tool pour sécuriser l'observation, pas pour autoriser rétroactivement l'action.

## Provider

Le kernel peut appeler un provider abstrait pour produire une décision.

La loop reste responsable du cycle : elle transmet le contexte au kernel, reçoit la décision, applique les contrôles et intègre les observations.

Contrainte : le provider reste derrière une interface abstraite et ne peut pas appeler de tool directement. Une demande de tool passe par une `Decision(kind="action")` puis par le chemin contrôlé.

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

## Forme retenue

La première loop est synchrone et centrée sur `run_once`.

Formes actuelles :

- `LoopState` est interne au tour et porte seulement les informations nécessaires à l'orchestration : budget de tools, observations publiques, artefacts, retries, tools indisponibles et garde-fous runtime.
- Les observations intermédiaires ne sont pas ajoutées à la session courte une par une. Elles sont renvoyées au provider via les métadonnées de l'`Intention` préparée pour l'itération suivante.
- Le résultat public du tour est un `RunResult` : décision finale, observation finale éventuelle et trace.
- Une attente utilisateur passe par un verdict guardian `ask` et un callback d'approbation fourni par le channel.
- La continuation après validation utilise `continue_after_approved_action`, sans faire contourner le chemin normal.

Les budgets de tools actuels sont bornés par profil :

- `safe` : 16 ;
- `limited` : 32 ;
- `power` : 64.

`SOUL.md` peut ajouter un petit bonus d'initiative en `safe` ou `limited`, sans dépasser le plafond `power`.

La trace de la loop reste événementielle. Elle doit montrer les étapes observables du travail sans devenir une mémoire durable ni exposer le raisonnement privé brut.

## Questions restantes

- Quelle quantité de garde-fous runtime peut rester dans `loop.py` avant de devoir extraire des helpers très ciblés ?
- Comment rendre les règles de retry et de livraison d'artefacts plus génériques sans créer un workflow engine ?
- Faut-il une variante asynchrone de la loop, ou les channels doivent-ils continuer à isoler l'asynchronisme autour de `run_once` ?
- Quel niveau de détail garder dans les événements `process` pour aider l'utilisateur sans transformer la trace en journal bruyant ?
