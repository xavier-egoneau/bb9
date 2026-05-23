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

Position provisoire : ne pas l'implémenter tant que le gateway local minimal ne fonctionne pas.

## Context-index

Le gateway peut construire ou interroger un index de contexte local.

Cet index doit rester une aide de recherche régénérable, idéalement en lecture seule pour le modèle, et ne doit pas devenir une mémoire durable.

## Frontière avec session

Le gateway peut recevoir un identifiant de session et rattacher ses observations à cette session, mais il ne devrait pas être le propriétaire principal de la session.

Position provisoire :

- `channel` reçoit le message ;
- `session` maintient le contexte court ;
- `loop` orchestre le cycle ;
- `gateway` exécute et observe les effets de bord.

## Questions à résoudre

- Quelle forme prend une action ?
- Comment distinguer action sûre, action sensible et action interdite ?
- Le gateway est-il synchrone au départ ?
- Comment tracer chaque action sans bruit excessif ?
- Où placer la logique de rollback éventuel ?
- Quelle information de session le gateway a-t-il le droit de connaître ?
- Comment représenter le workspace courant dans chaque action ?
