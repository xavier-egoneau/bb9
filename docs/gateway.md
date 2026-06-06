# Gateway

## Intention

Définir la frontière contrôlée entre le système agentique et le monde extérieur.

Le gateway exécute les actions concrètes demandées par le kernel ou la loop, avec garde-fous et observations.

Il ne reçoit que des actions déjà passées par le guardian.

## Contrat

Le gateway doit :

- recevoir des actions structurées ;
- vérifier qu'elles portent une autorisation explicite ;
- exécuter les effets de bord ;
- retourner une observation claire ;
- isoler les accès fichiers, shell, réseau et providers ;
- respecter le workspace courant par défaut ;
- logger les erreurs techniques sans exposer de secrets ;
- fournir les événements nécessaires à la trace sans exposer de secrets.

Le gateway ne doit pas :

- décider de l’objectif utilisateur ;
- cacher les échecs ;
- exécuter une action sensible sans validation ;
- mélanger permissions et logique métier ;
- accepter une action directement venue du modèle ;
- sortir du workspace sans permission explicite ;
- devenir propriétaire du contexte conversationnel complet.

## Mode continu et daemon

Le gateway peut participer à un mode continu, mais ce mode doit être lancé explicitement par l'utilisateur au départ.

Un daemon au démarrage de l'ordinateur est une option future, pas une condition d'usage.

Dans tous les cas :

- chaque action reste structurée ;
- les permissions passent par le guardian ;
- les observations sont rattachées à une session ou trace ;
- l'arrêt du processus doit rester simple.

## Frontière MCP

MCP peut devenir une frontière d'intégration pour exposer ou consommer des tools.

Position actuelle : ne pas l'implémenter sans usage réel. Le gateway local reste la frontière prioritaire ; MCP sera un adapter autour de ce contrat, pas un remplacement du noyau.

## Context-index

Le gateway peut construire ou interroger un index de contexte local.

Cet index doit rester une aide de recherche régénérable, idéalement en lecture seule pour le modèle, et ne doit pas devenir une mémoire durable.

## Forme actuelle

Le gateway est une façade d'exécution très fine.

`execute(action, context)` appelle le runtime du tool via `execute_runtime_tool`, puis retourne une `Observation`. Si aucun runtime ne correspond, il retourne une observation d'échec explicite.

Les effets de bord concrets vivent dans les archives de tools, pas dans `bb9/core/gateway.py`.

Cette finesse est intentionnelle :

- le kernel décide sans exécuter ;
- la loop orchestre ;
- le guardian autorise, demande ou bloque ;
- le gateway franchit la frontière d'exécution ;
- le runtime du tool porte l'implémentation concrète.

Le gateway reçoit un `RunContext` seulement pour transmettre le workspace et les informations runtime utiles au tool. Il ne possède pas la session, ne choisit pas le provider et ne modifie pas les permissions.

## Frontière avec session

Le gateway peut recevoir un identifiant de session et rattacher ses observations à cette session, mais il ne devrait pas être le propriétaire principal de la session.

Position retenue :

- `channel` reçoit le message ;
- `session` maintient le contexte court ;
- `loop` orchestre le cycle ;
- `gateway` exécute et observe les effets de bord.

## Questions restantes

- Faut-il ajouter plus tard un jeton d'autorisation explicite entre guardian et gateway, ou la décision portée par la loop suffit-elle tant que le gateway reste interne ?
- Où placer une logique de rollback éventuel pour les tools qui peuvent l'offrir ?
- Quels tools doivent devenir long-running ou asynchrones sans transformer le gateway en scheduler ?
- Quelle part de la trace d'action doit rester au gateway et quelle part doit rester dans la loop ?
