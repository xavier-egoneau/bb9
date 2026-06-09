# Channels

## Intention

Définir les surfaces d’entrée/sortie du système.

Un channel est une interface par laquelle un utilisateur ou un autre système échange avec l’agent.

Le kernel peut appeler un channel adapter, mais le channel reste responsable du transport concret.

## Contrat

Les channels doivent :

- recevoir une entrée ;
- transmettre une intention au système ;
- restituer une réponse ;
- préserver le même service fonctionnel que les autres surfaces autant que possible ;
- rester séparés de la logique décisionnelle du kernel ;
- indiquer le mode d'exécution demandé quand c'est utile ;
- pouvoir être remplacés sans changer le cœur.

Les channels ne doivent pas :

- contenir la logique décisionnelle ;
- exécuter directement des actions métier ;
- imposer une dépendance lourde au noyau ;
- mélanger transport, rendu et raisonnement.

## Alignement Des Surfaces

Les surfaces peuvent différer visuellement, mais elles doivent viser le même
service.

Un comportement disponible dans le CLI doit être disponible dans le chat web,
Telegram ou une future app quand le canal le permet. La forme peut changer :

- une trace peut être un bloc repliable en web, une section Markdown en CLI et
  un résumé court sur Telegram ;
- une todo list peut être affichée comme Markdown partout ;
- un rapport peut être un lien fichier en CLI, une pièce jointe en web et un
  résumé avec lien sur Telegram ;
- un diff peut être rendu riche dans le web, en carte repliable dans le CLI
  enrichi, en bloc Markdown dans le CLI simple, et en résumé ou fichier attaché
  sur Telegram.

Si une surface ne peut pas rendre une feature complète, elle doit fournir une
dégradation explicite : résumé, lien, fichier, artefact ou message indiquant la
limite du canal.

Les commandes REPL ne sont pas le modèle produit. Elles sont une syntaxe locale
du CLI pour appeler le même service. Une surface web ou Telegram peut exposer la
même capacité par bouton, texte naturel, menu, slash command ou Markdown, mais
le contrat reste le même.

Les surfaces ne doivent pas :

- inventer une logique métier différente ;
- masquer une capacité sans raison de canal ;
- laisser l'utilisateur croire que l'agent est inactif pendant un travail réel ;
- afficher directement une observation technique de tool comme réponse finale ;
- rendre le dashboard, le CLI ou Telegram propriétaire de la source de vérité.

Le service commun vit dans les contrats, la loop, les stores, les archives et
un noeud runtime partagé.
Le channel adapte seulement l'entrée, le rendu, les confirmations et les
contraintes propres au transport.

Dans l'implémentation Python, `bb9/core/runtime_service.py` porte ce noeud
partagé minimal :

- construire le `RunContext` ;
- exposer un statut runtime commun ;
- exécuter un message utilisateur via la loop ;
- assembler les artefacts transversaux d'un tour.

Les surfaces peuvent l'appeler directement quand elles vivent dans le même
processus, ou l'exposer via HTTP quand elles sont externes. La logique de
décision, de contexte et de run ne doit pas être recopiée dans chaque surface.

## Primitives De Rendu Conversationnel

Les surfaces doivent converger vers des primitives communes :

- `tool_trace` : outil demandé, statut, résumé humain, erreurs et preuve utile,
  sans exposer l'observation brute complète ;
- `activity_indicator` : animation ou état visible indiquant que l'agent est
  actif, en attente ou en train d'exécuter ;
- `live_tool_use` : marqueur temporaire montrant qu'un tool est en cours
  d'utilisation ;
- `code_block` : bloc typé (`bash`, `python`, `json`, etc.) avec copie quand le
  canal le permet ;
- `visible_process` : progression et résumé de réflexion visibles, sans exposer
  le raisonnement privé brut ;
- `todo_list` : liste Markdown cochable pour plans, tâches de travail et suites ;
- `diff` : changements de fichiers par tour, rendu riche si possible, Markdown
  ou fichier sinon ;
- `artifact_list` : fichiers, rapports, screenshots ou images produits, avec
  action `open`, `copy`, `download` ou équivalent ;
- `approval` : demande guardian avec action, risque, raison et choix possibles ;
- `error_detail` : résumé humain, détail technique repliable, suggestion de
  reprise ;
- `notification` : message durable, rattacheable à une session, une task, un
  cron ou un dream.

Ces primitives sont des contrats de service. Chaque channel décide ensuite du
rendu : composant web, Markdown, message Telegram, fichier attaché ou résumé.

`visible_process` peut être persisté dans l'historique visible avec le rôle
`process`. Il sert l'utilisateur et les surfaces ; il ne doit pas être ajouté
tel quel au contexte court du provider.

## États D'Activité

Une surface doit rendre visible l'activité de l'agent.

Quand l'agent travaille, l'utilisateur doit voir un état actif : animation,
spinner, ligne de statut, message de progression ou équivalent selon le canal.
Cet état signifie seulement que l'agent est occupé ; il ne révèle pas le
raisonnement privé.

Dans le CLI, cet état est un point de focus animé sur la ligne courante. Il se
nettoie avant la réponse finale, se suspend pendant une validation humaine et
change de libellé quand un tool est en cours.

Quand l'agent utilise un tool, la surface doit afficher un marqueur live
distinct, par exemple `shell en cours`, `tasks en cours`, `browser en cours` ou
un composant équivalent. Ce marqueur disparaît ou change d'état quand le tool
termine.

Quand un tool a terminé, la surface doit garder une trace différente du live :
statut `ok` ou `error`, nom du tool, résumé court et détail repliable si utile.
Cette trace terminée peut être persistée comme artefact `tool_trace`.

Pour le tool `shell`, le CLI affiche la commande demandée comme bloc `bash`
avant le statut de fin. La sortie brute reste une observation technique pour
l'agent ; l'utilisateur reçoit ensuite le bilan naturel de l'agent.

Le principe UX est simple : si l'agent est actif, ça doit se voir. Une longue
latence silencieuse est un défaut d'interface, même si le kernel travaille
correctement.

## Primitive `diff`

Un diff conversationnel est attaché au tour qui a modifié les fichiers.

Il doit être plié par défaut et afficher au premier niveau :

- le nombre de fichiers modifiés ;
- le total des lignes ajoutées et supprimées ;
- la liste des fichiers touchés avec leur compteur `+/-` ;
- une action `review`, `open` ou équivalent quand la surface le permet.

Chaque fichier peut ensuite être déplié séparément pour afficher ses hunks. Le
rendu riche peut ressembler à une carte de revue, mais le contrat reste simple :
un artefact `diff` par tour, avec des métadonnées suffisantes pour reconstruire
le résumé global et les lignes par fichier.

Dans le CLI, le diff immédiat reste compact : ligne `diff...`, fichiers touchés
et compteurs. Le patch complet reste attaché comme artefact et consultable via
`/history`.

Quand le canal ne peut pas afficher une revue riche, il doit dégrader vers :

- un résumé Markdown des fichiers modifiés ;
- un bloc diff Markdown si la taille le permet ;
- un fichier `.diff` ou `.patch` attaché si le diff est trop long ;
- un lien ou chemin vers l'artefact local.

## REPL

Le REPL est un channel local interactif.

Il fournit une interface d'extension minimale aux tools natifs :

- ajout de commandes slash ;
- interception locale d'une entrée avant provider ;
- traitement interactif d'un verdict guardian `ask` ;
- capture locale temporaire d'une valeur utilisateur ;
- ajout de lignes dans `/context`.

Un tool ou skill déclare ces extensions dans son `core.py` avec une fonction `register(cli)`. `cli.py` reste accepté par compatibilité.

Le REPL ne doit pas importer les fichiers métier d'un tool un par un. Il découvre les extensions via le chargeur générique.

### Rendu Markdown CLI

Le CLI peut rendre un sous-ensemble de Markdown quand le terminal supporte les
couleurs ANSI :

- titres ;
- listes simples et numérotées ;
- cases `[ ]` / `[x]` ;
- citations ;
- inline code ;
- emphase courte ;
- blocs de code encadrés ;
- coloration syntaxique légère dans les blocs `js`, `ts`, `json`, `python` et
  `bash`.

Quand la sortie n'est pas interactive, quand `NO_COLOR` est défini ou quand le
terminal est `dumb`, le CLI garde le Markdown brut. Le rendu visuel ne doit pas
remplacer le contenu : il améliore la lecture sans devenir une surface
propriétaire.

Les messages utilisateur doivent rester des ancres visuelles du fil sans être
recopiés après le prompt. Le CLI ajoute de l'air avant et après le tour agent,
sans modifier le contenu persisté ni le texte envoyé au provider.

## Chat Web Local

Le chat web local est un channel HTTP léger, servi uniquement sur `127.0.0.1`.

Son découpage reste volontairement simple :

- `bb9/api/` porte le service réutilisable et le transport HTTP JSON ;
- `bb9/chat-web/` porte l'interface statique qui consomme cette API.

L'interface statique doit rester portable. Elle est découpée en :

- `bb9-client.js` : client de transport ;
- `chat-ui.js` : état de conversation et orchestration UI ;
- `renderers.js` : rendu des messages, traces, artefacts et validations ;
- `app.js` : bootstrap du shell web local ;
- `app.css` : styles du shell web local.

Le point d'entrée portable est :

```js
createBb9Chat({
  root,
  client,
  capabilities,
})
```

Le web local utilise `httpBb9Client({apiBase: "/api"})`. Une future webview
VSCode ou app tierce peut fournir un autre client sans réécrire les renderers.

Les renderers ne doivent pas appeler directement `fetch` ni supposer que les
images passent par `/api/image`. Ils passent par le client ou par les
capabilities déclarées.

Les thèmes web personnalisés sont de simples fichiers CSS découverts dans cet
ordre :

- `<projet actif>/.bb9/themes/web/*.css` ;
- `~/.bb9/themes/web/*.css` ;
- `bb9/chat-web/themes/*.css`.

Un fichier `solar.css` déclare le thème `solar` et peut cibler
`:root[data-theme="solar"]`.

Le composer web doit rester ergonomique pour un usage quotidien :

- focus automatique sur l'entrée ;
- `Enter` envoie, `Shift+Enter` ajoute une ligne ;
- pendant un run, le bouton d'envoi devient un stop ;
- pendant un run, `Enter` ajoute le brouillon à une queue éditable avant envoi ;
- pendant une validation guardian en attente, les nouvelles demandes restent en queue locale et ne remplacent pas la validation courante ;
- actions fréquentes sous l'entrée ;
- bouton d'envoi sous forme d'icône ;
- réglage rapide et appliqué au changement du profil de sécurité, du modèle et du niveau de raisonnement ;
- sélection du modèle depuis les providers configurés, groupée par provider quand plusieurs existent ;
- thème choisi côté surface ;
- accès aux projets connus et aux sessions web du projet actif ;
- panneau Git du projet actif : branche, nombre de fichiers modifiés, détail compact et diff dépliable par fichier ;
- autocomplétion des commandes slash natives et des commandes d'archives du projet actif ;
- découverte des thèmes web fournis par le produit, l'utilisateur et le projet actif.

Une session web est rattachée à un `project_path`. Le projet actif filtre
`/api/sessions` et `/api/history`. Quand le projet actif change depuis
l'interface, le serveur change aussi son workspace d'exécution vers ce dossier,
puis recharge sessions, skills locaux, thèmes, état Git et plan courant depuis ce
nouveau workspace. Ce switch runtime est refusé pendant un run actif.

Au lancement, si le port demandé sert déjà un BB9 web local d'un autre projet,
`bb9 web` demande à ce serveur de basculer vers le dossier courant via
`/api/project`, puis réutilise la même URL. Si le switch runtime est refusé ou
indisponible, le nouveau serveur démarre sur le port suivant afin de ne pas
ouvrir silencieusement le mauvais dossier.

Le projet choisi dans l'interface web est mémorisé dans les settings utilisateur.
Au redémarrage, le serveur reprend ce dernier projet s'il existe encore, même si
le terminal de lancement est resté dans un autre dossier. Sans projet persistant
valide, le dossier courant reste le fallback.

Il doit :

- recevoir un message via `/api/chat` ;
- demander l'arrêt du run courant via `/api/stop` ;
- reprendre une validation guardian via `/api/approval` ;
- exposer l'état courant via `/api/status` ;
- lister les projets connus via `/api/projects` ;
- choisir le projet actif et workspace d'exécution via `/api/project` ;
- lister les commandes disponibles via `/api/commands` ;
- lister les thèmes disponibles via `/api/themes` et servir un thème CSS via `/api/theme` ;
- lister les modèles disponibles par provider via `/api/models` ;
- exposer l'état Git du projet actif via `/api/git` ;
- exposer le diff textuel d'un fichier modifié via `/api/git/diff` ;
- changer de branche Git via `/api/git/branch`, sans forcer ni masquer les erreurs Git ;
- refuser le changement de branche tant que le worktree contient des changements non commités ou non stashés ;
- annoncer les capacités web disponibles dans `/health`, dont `git-api` et `git-diff-api` ;
- relire l'historique visible de la session active via `/api/history` ;
- accepter des images via `/api/upload` et les convertir en références `[image: ...]` ;
- transformer ce message en `Intention` avec les mêmes helpers que le CLI ;
- construire un `RunContext` normal ;
- appeler `run_once` ;
- conserver la session courte du channel ;
- persister le tour dans l'historique visible avec `source=web` ;
- retourner la réponse, les événements utiles et les artefacts du tour.

Il ne doit pas :

- appeler directement les tools ;
- embarquer une logique agentique propre ;
- devenir un dashboard ;
- introduire de framework web lourd.

La surface web expose aussi un état live via `/api/run/events`. Elle affiche
l'état runtime minimal, les messages persistés, un processus visible public, les
événements de tools utiles, les artefacts simples, les validations guardian et
les images jointes par bouton, collage ou glisser-déposer.
Après un rechargement de page, elle doit reconstruire l'indicateur de run actif
depuis `/api/status`, reprendre la trace live via `/api/run/events`, puis relire
`/api/history` quand le run se termine afin d'afficher la réponse persistée.
Dans la timeline web, la couleur doit rester lisible : gris pour une étape
interne passée, jaune animé pour une action ou un subagent actif/en attente,
vert pour un état explicitement terminé, rouge pour une erreur ou un blocage.
La trace live et la trace finale forment une seule stack de tour : ouverte
pendant que BB9 travaille, repliée sous le bilan final quand le run est terminé.
Dans le panneau de plan, les blocages de dépendance doivent être distingués des
erreurs directes afin qu'un run partiellement bloqué ne ressemble pas à un
échec total.

Restent hors première tranche :

- commandes de changement de provider, modèle, agent et profil ;
- rendu Markdown riche, diffs repliables et actions fichier ;
- reprise de session choisie par l'utilisateur.

## Questions à résoudre

- Quelles features minimales chaque surface doit-elle exposer dès le départ ?
- Quel format commun utiliser pour traces visibles, artefacts, confirmations et notifications ?
- Comment déclarer proprement qu'une surface ne supporte qu'une dégradation ?
- Comment représenter une session ?
- Comment gérer le streaming ou les réponses longues ?
- Comment distinguer utilisateur local, API externe et routine planifiée ?
- Comment exposer clairement le choix entre exécution ponctuelle, mode continu et daemon optionnel ?
