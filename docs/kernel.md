# Kernel

## Intention

Définir le cerveau léger du système agentique.

Le kernel est le point d'entrée logique du système. Tout passe conceptuellement par lui, mais il reste léger : il appelle ou coordonne les autres briques sans absorber leurs responsabilités.

Il transforme une intention utilisateur et un contexte disponible en décision exploitable, sans gérer directement les effets de bord.

S'il propose une action, cette action reste une demande structurée. Elle ne peut pas atteindre un tool sans passer par la loop, les hooks, le guardian et le gateway.

## Contrat

Le kernel doit :

- servir de point d'entrée logique au système ;
- recevoir une intention ;
- demander aux channels de fournir ou restituer les messages ;
- demander aux providers de produire une réponse via une interface abstraite ;
- lire ou recevoir un contexte structuré ;
- produire une décision compréhensible ;
- exprimer les actions souhaitées sans les exécuter directement ;
- pouvoir exprimer une délégation future sans l'exécuter directement ;
- rester indépendant des interfaces, providers, channels et outils concrets.

Le kernel ne doit pas :

- écrire directement dans le système de fichiers ;
- appeler directement un tool ;
- écouter lui-même les channels ;
- gérer les détails concrets des connexions providers ;
- gérer les permissions ;
- contenir de logique UI ou réseau.

## Provider

Le kernel peut appeler un provider abstrait passé en dépendance.

Dans ce cas :

- il ne connaît pas les détails concrets du provider ;
- il ne gère pas les secrets ;
- il ne choisit pas les permissions ;
- il ne contourne pas la loop, le guardian ou le gateway.

Le fichier `providers` garde la responsabilité des adaptateurs concrets.

## Channels

Le kernel peut appeler un channel adapter, mais il ne doit pas écouter lui-même une CLI, un serveur HTTP, un fichier inbox ou un autre transport.

Le fichier `channels` garde la responsabilité de recevoir une entrée et de restituer une réponse.

## Memory et context-index

La memory et le context-index sont des sources de contexte pour le kernel.

Le kernel peut les lire sous forme de contexte préparé, mais il ne doit pas devenir propriétaire de leur stockage, de leur ingestion ou de leur rafraîchissement.

## Contexte provider initial

Quand un provider est branché, le kernel construit un contexte court composé de :

- l'instruction runtime minimale de BB9 ;
- le profil d'autonomie courant (`safe`, `limited` ou `power`) ;
- le profil agent Markdown chargé ;
- l'historique court de session ;
- le workspace-status volatil ;
- le context-index Markdown régénérable du workspace ;
- les index Markdown actifs des skills et tools ;
- les skills `always` en contenu complet ;
- une frontière de tour indiquant que l'intention courante prime sur la session récente ;
- l'intention courante.

Le kernel ne persiste rien lui-même. La session reste portée par le channel/CLI et la memory durable reste explicite.

`IDENTITY.md` et `SOUL.md` ne sont pas des métadonnées décoratives. Ils définissent le contexte d'identité actif de l'agent : posture, ton, autonomie, limites et manière de travailler. Quand l'utilisateur demande le contexte disponible, l'agent doit aussi mentionner les éléments utiles de cette identité.

Le kernel peut aussi transformer `SOUL.md` en contrat comportemental court avant l'appel provider. Ce contrat ne remplace pas le fichier source ; il rend explicite ce que le runtime attend du modèle :

- prendre l'initiative dans le workspace quand le soul demande de la débrouillardise ;
- demander directement une action contrôlée quand une lecture manque ;
- garder les limites de prudence sur secrets, hors workspace, suppressions durables et actions extérieures ;
- exprimer un avis technique quand cela aide la décision.

Le profil d'autonomie doit réduire les réponses timides :

- en `safe`, BB9 reste prudent mais utilise les lectures simples au lieu de demander à l'utilisateur de les faire ;
- en `limited`, BB9 avance sur les lectures et vérifications courantes ;
- en `power`, BB9 demande directement les actions utiles dans le workspace ou les trusted roots, sans terminer par une permission conversationnelle du type "si tu veux".

Quand BB9 mentionne une limite de contexte, il ne doit pas en faire une conclusion passive. Si la limite compte vraiment, il la transforme en prochain pas concret ; sinon il la garde comme nuance courte.

La loop peut accorder un petit budget de tool supplémentaire si `SOUL.md` demande explicitement de l'initiative ou de la débrouillardise. Cela rend la posture active observable dans l'exécution, sans contourner le guardian ni dépasser le plafond du profil `power`.

## Réponses D'Analyse

Quand l'utilisateur demande d'analyser un repo, projet ou dossier, BB9 doit
produire une synthèse utile, pas un inventaire.

La réponse doit privilégier :

- la nature du projet ;
- le verdict global ;
- les qualités et risques principaux ;
- les priorités d'amélioration ;
- les fichiers ou APIs seulement quand ils appuient une conclusion.

La réponse ne doit pas commencer par vider l'arborescence, lister toutes les
méthodes ou recopier les observations de lecture. Les listings de fichiers sont
réservés aux demandes explicites de structure, d'inventaire ou d'audit détaillé.

## Questions de contexte

Quand l'utilisateur demande ce que BB9 a en contexte, le kernel peut répondre directement depuis `RunContext` sans appeler le provider.

Cette réponse déterministe doit inclure les éléments utiles déjà chargés :

- agent actif ;
- `IDENTITY.md` et `SOUL.md` actifs ;
- profil d'autonomie ;
- workspace courant ;
- carte locale du workspace ;
- tools et skills disponibles ;
- session courte si elle existe.

Raison : cette question porte sur l'état runtime réel. La confier au provider rend la réponse plus variable et peut produire une posture trop timide, comme conclure par une absence de lecture détaillée au lieu de présenter le contexte exploitable.

## Demande de tool par le provider

Le provider ne peut pas appeler un tool directement.

S'il a besoin de lire le workspace, il peut seulement demander une action structurée sous forme textuelle stricte :

```text
BB9_ACTION shell <commande>
```

Une réponse provider qui demande un tool doit contenir une seule action `BB9_ACTION`.
Elle ne doit pas ajouter de prose avant ou après cette action dans le même message,
ni coller deux actions ensemble. La loop renvoie l'observation au provider, qui peut
ensuite demander l'action suivante si nécessaire.

Pour `shell`, le corps de l'action doit être une commande pure. Une phrase naturelle
collée à la commande rend l'action malformée et peut être bloquée avant exécution.

S'il doit ajouter un secret, il ne doit jamais demander la valeur dans la conversation. Il peut seulement demander :

```text
BB9_ACTION secret add <NOM_DE_VARIABLE>
```

S'il doit lire ou diagnostiquer l'agenda CalDAV local, il peut demander :

```text
BB9_ACTION caldav doctor
BB9_ACTION caldav agenda days=7
```

Le kernel transforme cette demande en `Action`.
La loop envoie ensuite l'action aux hooks, au guardian puis au gateway.
L'observation produite est renvoyée au provider pour obtenir une réponse finale.

La première boucle limite le nombre de demandes tool par tour et refuse les commandes composées via le guardian.

## Questions à résoudre

- Quelle forme prend une intention ?
- Quelle forme prend une décision ?
- Le kernel retourne-t-il une réponse finale, une action, ou les deux ?
- Une délégation vers subagent est-elle une action, une décision, ou un type séparé ?
- Comment représenter l’état courant sans créer une machine trop lourde ?
- Quelle partie du raisonnement doit être tracée ?
