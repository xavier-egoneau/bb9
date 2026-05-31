# Decisions

## 2026-05-22 — Markdown d'abord pour les briques système

Décision : décrire les briques du système dans `docs/` avant d'écrire du code.

Raison : clarifier les intentions, contrats et questions ouvertes sans figer trop tôt une architecture Python.

Conséquence : chaque brique importante commence par un document court avant toute implémentation. Markdown-first est un principe structurel du projet, pas un skill activable par agent.

## 2026-05-22 — `AGENTS.md` est pour les contributeurs IA

Décision : `AGENTS.md` décrit les règles pour les agents qui travaillent sur le dépôt, pas les agents internes du produit.

Raison : éviter la confusion entre gouvernance du repo et architecture runtime.

Conséquence : les agents du produit seront décrits ailleurs si le besoin apparaît.

## 2026-05-22 — Python pressenti comme runtime minimal

Décision : Python reste le choix pressenti pour le runtime, mais uniquement comme couche d'exécution minimale.

Raison : Python est pratique pour lire/écrire des fichiers, appeler des providers, parser des formats, exposer une CLI/API et prototyper rapidement.

Conséquence : le projet évite les packages complexes et les frameworks agentiques ; le code doit rester subordonné aux contrats Markdown.

## 2026-05-22 — Séparation kernel / loop / gateway / guardian / session

Décision : les responsabilités système sont séparées dès la conception.

Raison : éviter qu'un module central devienne un mélange de décision, exécution, permissions, transport et mémoire.

Conséquence : le kernel décide, la loop orchestre, le gateway exécute, le guardian autorise ou bloque, la session porte le contexte court.

## 2026-05-22 — Subagents prévus dès la conception

Décision : les subagents sont pris en compte dans les contrats dès le départ, sans implémenter immédiatement un système multi-agent.

Raison : les agents récents convergent vers des délégations bornées avec contexte, tools et permissions séparés. Le projet doit pouvoir accueillir ce motif sans refonte.

Conséquence : une brique `subagents` est documentée. La phase 1 reste centrée sur une boucle simple, mais les formes d'intention, action, observation et trace ne doivent pas empêcher une future délégation.

## 2026-05-22 — Mode continu optionnel, daemon différé

Décision : le système pourra proposer un mode continu lancé explicitement par l'utilisateur. Un daemon au démarrage reste optionnel et différé.

Raison : le mode always-on peut être utile, mais il augmente fortement les risques liés aux permissions, aux cron, aux secrets et aux instructions dormantes.

Conséquence : cron, mode continu et daemon doivent passer par le guardian et rester désactivables. Aucun lancement automatique ne doit être requis pour utiliser le système.

## 2026-05-22 — Guardian avant les tools

Décision : aucune action proposée par un provider, le kernel ou un subagent ne doit atteindre un tool directement. Toute action passe par la loop, un pre-action hook, le guardian puis le gateway.

Raison : le modèle peut produire une action hors périmètre, dangereuse ou trop ambiguë. Le blocage doit avoir lieu avant tout effet de bord.

Conséquence : le guardian autorise, demande confirmation ou bloque avant exécution. Le post-action hook intervient après le tool pour vérifier l'observation, masquer les secrets et tracer. Le gateway n'exécute que des actions structurées et autorisées.

## 2026-05-22 — Memory, trace, session et context-index séparés

Décision : la mémoire durable, la session courte, la trace d'exécution et les index de contexte sont des concepts distincts.

Raison : les agents récents mélangent souvent mémoire personnelle, historique, contexte indexé et traces. Ce mélange rend les permissions, la suppression et l'audit difficiles.

Conséquence : des contrats dédiés existent pour `memory`, `trace`, `session` et `context-index`. Un index local peut aider à s'orienter, mais il reste régénérable et ne devient pas une mémoire durable.

Amendement 2026-05-25 : la mémoire durable est un store SQLite local en forme de graphe léger. Les nœuds portent les faits durables, les arêtes portent les relations typées, et les scopes `global` / `project` déterminent l'injection future. Le Markdown reste le lieu des contrats et politiques ; SQLite porte l'état durable requêtable que le dreaming doit consolider.

Amendement 2026-05-25 : les sessions sont persistées dans `~/.bb9/sessions.db`, séparément de la mémoire durable. La session reste le contexte court actif ; le store de sessions garde l'historique récent, les résumés de compaction et le rattachement projet pour que le dreaming puisse consolider sans transformer automatiquement une conversation en mémoire.

## 2026-05-22 — Workspace comme frontière locale

Décision : le workspace est la frontière locale par défaut pour les lectures, écritures et commandes d'une tâche agentique.

Raison : les outils récents isolent les runs dans des workspaces pour limiter les effets de bord, comparer les résultats et demander confirmation avant de sortir du périmètre.

Conséquence : la phase 1 peut utiliser le dépôt courant comme workspace simple. Les worktrees, agents parallèles, scripts setup/run/teardown et automations restent des options futures.

## 2026-05-22 — `DOC.md` supprimé

Décision : supprimer `DOC.md` pour éviter une documentation conceptuelle parallèle aux contrats de `docs/`.

Raison : les sujets étaient déjà documentés dans les fichiers spécialisés. Un résumé transversal risquait de contredire les contrats.

Conséquence : `README.md` garde la vision et la carte courte du dépôt. Les contrats détaillés vivent dans `docs/`.

## 2026-05-22 — Kernel comme point d'entrée logique léger

Décision : le kernel est le point d'entrée logique du système et le cerveau décisionnel, mais il reste léger.

Raison : tout doit pouvoir passer conceptuellement par le kernel sans qu'il devienne propriétaire des channels, providers, tools, permissions ou effets de bord.

Conséquence : le kernel peut appeler des adapters comme `channels` ou `providers`, mais il ne gère pas leur transport ou connexion concrète. Il produit des décisions structurées ; la loop orchestre le cycle ; le guardian autorise ; le gateway exécute.

## 2026-05-22 — Logs distincts de la trace

Décision : les logs runtime sont distincts de la trace agentique.

Raison : les logs aident à diagnostiquer le code, tandis que la trace raconte une exécution agentique. Les mélanger rendrait le système bruyant et moins lisible.

Conséquence : un contrat `logs` existe. L'implémentation initiale utilise la bibliothèque standard Python, sans dépendance externe.

## 2026-05-22 — Runtime nommé `bb9`

Décision : le premier runtime Python vit dans `bb9/`.

Raison : `app/` est trop générique et évoque une application web. `bb9` est court, importable et donne une identité légère au système.

Conséquence initiale : le code Python démarre dans un package plat `bb9`, sans structure `src/` ni sous-packages prématurés.

Amendement 2026-05-23 : le runtime pur vit maintenant dans `bb9/core/`. Les archives natives vivent dans `bb9/tools/`. `bb9/__main__.py` et `bb9/cli.py` restent des points d'entrée compatibles.

## 2026-05-22 — Provider OpenAI-compatible d'abord

Décision : le premier provider réel est un adapter OpenAI-compatible minimal, sans dépendance externe.

Raison : beaucoup de services exposent une API compatible OpenAI, dont OpenAI, OpenRouter, Ollama, LM Studio et vLLM. Cela donne une surface flexible sans installer LiteLLM ou AISuite tout de suite.

Conséquence : `bb9.providers.OpenAICompatibleProvider` utilise la bibliothèque standard Python. LiteLLM ou AISuite restent des adapters optionnels futurs si le besoin réel apparaît.

## 2026-05-22 — Ancienne décision remplacée : agents Markdown dans le repo

Décision remplacée le 2026-05-23 : les agents actifs vivent dans `~/.bb9/agents/<name>/`. Le repo BB9 ne garde que des templates dans `bb9/templates/agents/<name>/`.

Décision initiale : les agents internes du produit sont décrits en Markdown dans `agents/<name>/`.

Raison : le projet est Markdown-first. L'identité et la posture d'un agent doivent être inspectables et modifiables sans toucher au code. Elles relèvent surtout de la manière de travailler de l'utilisateur, donc du dossier user.

Conséquence : un agent minimal contient `IDENTITY.md` et `SOUL.md`. Le kernel reçoit un agent chargé en contexte, mais ne parcourt pas lui-même le disque. L'installation copie les templates dans `~/.bb9/agents/` seulement s'ils sont absents.

Amendement : `IDENTITY.md` et `SOUL.md` sont un contexte d'identité actif, pas une documentation secondaire. Le prompt runtime doit dire explicitement au provider d'appliquer cette posture et de la mentionner quand l'utilisateur demande le contexte disponible.

Amendement : quand l'utilisateur demande ce que BB9 a en contexte, le kernel répond directement depuis `RunContext` sans appeler le provider. Cette question décrit l'état runtime réel ; une réponse déterministe évite les formulations timides ou variables du modèle.

Amendement : `SOUL.md` influence aussi l'exécution. Le kernel en dérive un contrat comportemental court avant l'appel provider, et la loop peut accorder un petit budget de tool supplémentaire si le soul demande explicitement initiative, débrouillardise ou audace dans le workspace. Cette influence ne contourne jamais le guardian.

## 2026-05-22 — Ancienne décision remplacée : skills Markdown globaux

Décision remplacée le 2026-05-23 : les skills ne vivent plus dans le dépôt BB9. Ils vivent dans `~/.bb9/skills/<name>/SKILL.md` comme extensions utilisateur.

Décision initiale : les skills vivent dans `skills/<name>/SKILL.md` et sont actifs par défaut pour tous les agents.

Raison : les skills sont des règles de comportement réutilisables. Les rendre globaux évite de dupliquer du Markdown dans chaque agent.

Conséquence actuelle : un agent peut toujours désactiver des skills avec `~/.bb9/agents/<name>/SKILLS_DISABLED.md`, et la source des skills est le dossier utilisateur `~/.bb9/skills/`.

## 2026-05-22 — Tools Markdown globaux, désactivation par agent

Décision : les tools déclarés vivent dans `bb9/tools/<name>/TOOL.md` et sont disponibles par défaut pour tous les agents.

Raison : les tools sont des capacités globales, mais certains agents doivent pouvoir travailler avec un périmètre réduit.

Conséquence : un agent peut désactiver des tools avec `~/.bb9/agents/<name>/TOOLS_DISABLED.md`, sous forme de liste Markdown. Cette désactivation limite les tools présentés au modèle, sans remplacer le guardian.

## 2026-05-22 — Subagents locaux avec héritage

Décision : les subagents vivent dans `~/.bb9/agents/<agent>/subagents/<subagent>/` et reprennent la structure Markdown d'un agent.

Raison : un subagent est une spécialisation locale d'un agent parent. Il doit pouvoir hériter du parent et ne redéfinir que ce qui change.

Conséquence : `IDENTITY.md` et `SOUL.md` héritent du parent s'ils sont absents. Les fichiers `SKILLS_DISABLED.md` et `TOOLS_DISABLED.md` s'ajoutent à ceux du parent.

Amendement 2026-05-23 : un subagent `default` sert de fallback de delegation bornee quand aucune specialisation ne correspond mieux. Le runtime genere `subagents/INDEX.md` depuis les subagents disponibles et l'injecte dans le contexte du parent pour eviter un choix implicite ou hasardeux.

Amendement : le subagent `goal` est le worker conventionnel de `/goal`. Le runner l'utilise s'il existe, retombe sur `default` sinon, puis sur l'agent courant. L'evaluateur de goal reste une brique runtime separee, pas un subagent libre.

Amendement : `MODEL.md` permet a un agent ou subagent de surcharger uniquement le modele, en reutilisant le provider et l'authentification actifs. Le cas d'usage prioritaire est `subagents/goal`, qui peut tourner sur un modele plus leger pour optimiser les iterations.

Amendement : `MODEL.md` peut aussi porter `ReasoningEffort`. Cette valeur est heritee par les subagents si absente et transmise au provider quand elle est renseignee.

## 2026-05-22 — Ancienne décision remplacée : exploration projet comme skill

Décision remplacée le 2026-05-23 : `project-explorer` et `project-onboarding` sont des tools documentaires natifs, car ils font partie de l'archive BB9 et doivent rester partageables avec elle.

Décision initiale : l'exploration d'un projet est un skill, pas un tool. Le tool atomique correspondant est `shell`.

Raison : explorer un projet est une méthode de travail qui utilise plusieurs commandes et produit une synthèse. Le shell est la capacité concrète qui exécute une commande bornée.

Conséquence actuelle : `bb9/tools/project-explorer` et `bb9/tools/project-onboarding` décrivent la méthode. `bb9/tools/shell` décrit la capacité d'exécution, toujours soumise au guardian.

## 2026-05-22 — Ancienne décision remplacée : tools atomiques, skills méthodologiques

Décision remplacée le 2026-05-23 : un tool ou un skill peut porter une capacité ou un comportement attendu. La frontière principale devient leur lieu et leur statut : tool natif dans l'archive BB9, skill utilisateur dans `~/.bb9/skills/`.

Décision initiale : un tool doit rester une capacité atomique d'exécution. Un skill décrit une méthode, une posture ou une manière d'utiliser des capacités.

Raison initiale : mélanger orchestration et exécution rendrait les permissions opaques et ferait grossir les tools en mini-agents.

Conséquence actuelle : cette séparation stricte n'est plus retenue. Une brique livrée avec BB9 peut être un tool documentaire ou exécutable, mais toute action concrète reste soumise au guardian avant le gateway.

## 2026-05-23 — Profils de permission et trusted roots

Décision : le guardian combine profil de permission, zone de chemin et risque d'action.

Raison : un dossier hors workspace peut être un autre projet légitime. Il doit pouvoir être autorisé durablement sans ouvrir toute la machine.

Conséquence : les profils sont `safe`, `limited` et `power`. Les dossiers validés par l'utilisateur deviennent des trusted roots persistants dans `~/.bb9/trusted-roots.md`, dans le dossier user. Dans un workspace ou trusted root, l'écriture normale est autorisée ; les actions sensibles restent `ask` ou `block`.

Amendement vocabulaire : le repo désigne ce dépôt BB9 ; le dossier user désigne `~/.bb9/` ; un workspace désigne le dossier dans lequel BB9 est lancé pour travailler. Les trusted roots relèvent du dossier user, pas du workspace, car ils expriment une confiance durable de l'utilisateur.

Amendement : le profil choisi avec `/profil` est persistant dans `~/.bb9/settings.json`. L'option `--profile` surcharge ce choix seulement pour le lancement courant.

## 2026-05-23 — Shell sans `shell=True`

Décision : le premier tool `shell` exécute uniquement des commandes parsées en arguments, sans `shell=True`.

Raison : garder une surface d'exécution minimale et éviter les enchaînements opaques, redirections et expansions shell dangereuses.

Conséquence : les commandes composées demandent confirmation. Les commandes de lecture connues peuvent être exécutées dans le workspace ou un trusted root. Les chemins protégés sont bloqués.

Amendement : une commande destructive explicitement demandée dans le workspace, par exemple supprimer un fichier de travail, doit être soumise au guardian plutôt que refusée par le modèle. Le guardian demande validation pour l'action sensible et bloque les chemins protégés avant toute validation.

Amendement : les commandes shell d'écriture simples et explicitement connues (`touch`, `mkdir`) sont des écritures normales dans le workspace ou un trusted root. Elles peuvent être autorisées sans validation, notamment en profil `power`. Les chemins hors périmètre demandent validation et les chemins protégés restent bloqués.

Amendement : les lectures shell courantes ne doivent pas devenir une succession d'asks en `limited` ou `power`. `grep` fait partie des commandes de lecture connues. Les pipelines de lecture simples peuvent être réécrits en argv direct (`cat fichier | head -20` -> `head -20 fichier`) afin de rester sans `shell=True` tout en évitant les confirmations de confort.

Amendement : dans l'usage agentique, `grep` avec code retour `1` et sans sortie signifie "aucune correspondance", pas une panne du tool. L'observation doit donc être `ok` avec résumé `no matches`, afin de ne pas afficher un faux `shell error`.

Amendement : `python3 -m http.server <port>` est une commande longue reconnue de prévisualisation locale. En `limited` et `power`, elle peut démarrer en arrière-plan dans le workspace sans ask, avec bind forcé à `127.0.0.1` si absent. Ce n'est pas une commande de test courte et elle ne doit pas finir en timeout.

## 2026-05-31 — Service runtime partagé pour les surfaces

Décision : les surfaces doivent consommer un noeud runtime commun plutôt que reconstruire chacune leur contexte et leur cycle de run.

Raison : CLI, web local et futurs adapters externes doivent offrir le même service fonctionnel sans dupliquer la logique de contexte, provider, loop ou artefacts.

Conséquence : `bb9/core/runtime_service.py` porte les appels communs minimaux : construction du `RunContext`, statut runtime, exécution d'un message et assemblage des artefacts transversaux. Les surfaces restent responsables du transport, du rendu, des validations humaines et de la persistance propre au canal.

## 2026-05-31 — Chat web portable par client et renderers

Décision : `bb9/chat-web/` doit rester une surface portable, pas une application locale couplée à `localhost`.

Raison : la même interface devra pouvoir être embarquée plus tard dans une webview VSCode, une app tierce ou une autre surface, avec un transport différent.

Conséquence : le chat web est découpé en shell HTML, styles, client de transport, orchestration UI et renderers. Le point d'entrée portable est `createBb9Chat({ root, client, capabilities })`. Le web local fournit `httpBb9Client({ apiBase: "/api" })`, tandis qu'une future surface pourra fournir un autre client.

Amendement : un serveur local de prévisualisation ne retourne `ok` qu'après validation d'une réponse HTTP réelle. Si le port est déjà occupé par un serveur qui répond, BB9 le réutilise. Si le process démarre mais ne répond pas, BB9 le termine et renvoie une erreur claire au lieu de laisser `browser` découvrir un `ERR_EMPTY_RESPONSE`.

Amendement : si le port demandé pour `python3 -m http.server <port>` est occupé par un serveur muet ou indisponible, le tool `shell` essaie automatiquement les ports suivants et retourne l'URL réellement servie. L'agent doit utiliser cette URL, pas demander à l'utilisateur s'il faut essayer un autre port.

Amendement : les modifications de fichiers ne doivent pas être simulées par du shell de lecture ou des promesses de "prochaine action". Le tool natif `files` porte les opérations bornées (`write`, `replace`, `insert_before`, `insert_after`) dans le workspace ou les trusted roots. En `limited` et `power`, ces écritures bornées sont autorisées sans ask dans le périmètre.

Amendement : le filtrage des placeholders BB9_ACTION ne doit pas confondre HTML et placeholders de protocole. Une action `files` contenant `</head>` ou `<link ...>` est valide ; seuls les placeholders explicites comme `<commande>`, `<path>` ou `<texte>` sont ignorés.

## 2026-05-23 — CLI interactif sans dépendance externe

Décision : le premier CLI interactif vit dans `bb9/core/cli.py` et utilise seulement la bibliothèque standard.

Raison : le système doit être agréable à utiliser, mais rester minimal et portable.

Conséquence : `python3 -m bb9` sans intention ouvre un REPL avec commandes slash. `python3 -m bb9.cli` lance le même mode interactif. Le REPL expose seulement les commandes utiles à l'utilisateur ; les outils et réglages internes restent pilotés par le runtime ou par options de lancement.

## 2026-05-23 — Index Markdown générés pour skills et tools

Décision : `~/.bb9/skills/INDEX.md` et `bb9/tools/INDEX.md` sont générés depuis les fichiers sources.

Raison : une liste maintenue à la main dériverait rapidement. Le kernel a besoin d'un contexte court sans injecter tous les fichiers complets.

Conséquence : les indexes résument les skills utilisateur et tools natifs actifs. Ils sont régénérés au lancement de `bb9`. Les skills `always` peuvent être injectés en complet ; les tools restent résumés par défaut.

## 2026-05-23 — Provider config reprise de Marius, mais réduite

Décision : reprendre la logique Marius de configuration provider sous une forme minimale : registre, config locale, references de secrets, recuperation des modeles et assistant `/model`.

Raison : le choix provider/auth/modele est une vraie brique utilisateur. Il doit fonctionner pour les API keys, mais aussi laisser une place explicite aux auth web type ChatGPT/Codex.

Conséquence : `bb9.core.provider_config` contient cette brique sans dependance externe. La config provider stocke le provider actif et des references de secrets, pas des secrets bruts. L'auth web ChatGPT/Codex est portee depuis Marius sous forme experimentale : tokens locaux dans `~/.bb9/secrets/`, adapter runtime dedie, et fallback API key/OpenRouter recommande si le flux web change.

Amendement : la config provider par defaut devient utilisateur (`~/.bb9/providers.json`) afin que BB9 fonctionne depuis n'importe quel workspace apres installation editable. `.bb9/providers.json` ne doit plus être choisi automatiquement ; une surcharge doit être explicite via option ou variable d'environnement.

## 2026-05-23 — Historique court de session dans le contexte provider

Décision : la session CLI conserve un historique court et borné des tours utilisateur/assistant, injecté au provider par le kernel.

Raison : un agent conversationnel inutilisable sans continuité forcerait l'utilisateur à répéter le contexte. Cette continuité doit rester temporaire et séparée de la memory durable.

Conséquence : `Session` porte des messages récents en mémoire. `/new` repart sur une session vide. Le kernel lit ce contexte mais ne le persiste pas et ne l'écrit pas dans `MEMORY.md`.

Amendement : la session peut être compactée. `/compact` force une compaction locale du contexte court, et une auto-compaction se déclenche quand la session devient trop longue. La compaction produit un résumé dérivé interne, conserve les messages récents et ne modifie pas la mémoire durable.

Amendement : l'auto-compaction s'appuie sur une resolution automatique des metadonnees de modele, mais sans requete web implicite. BB9 garde un cache dans `~/.bb9/model-metadata.json`, utilise une table connue embarquee, puis un fallback prudent. Le seuil cible est environ 80% de la fenetre de contexte ou une limite souple d'entree si elle existe. Une mise a jour web devra passer par une commande ou un tool explicite.

## 2026-05-23 — BB9 utilisable depuis n'importe quel workspace

Décision : ajouter un `pyproject.toml` minimal et un installateur utilisateur `bb9/install.py`, lançable avec `python3.11 -m bb9.install`.

Raison : BB9 doit pouvoir être lancé dans le dossier du projet à explorer, pas seulement depuis son propre dépôt.

Conséquence : `python3.11 -m bb9.install` ou `py -3.11 -m bb9.install` expose le dépôt via le user-site Python, crée le lanceur `bb9` dans le dossier de commandes utilisateur, ajoute ce dossier au `PATH` utilisateur quand c'est possible, crée `~/.bb9/` avec `agents/`, `skills/`, `goals/` et `secrets/`, puis migre la config provider vers `~/.bb9/`. Le lanceur réutilise l'exécutable Python ayant lancé l'installateur. Les agents actifs et les skills viennent du dossier utilisateur ; les tools natifs viennent de `bb9/tools/`.

Amendement : le packaging standard doit rester valide aussi. `pip install -e .` expose la console script `bb9` via `pyproject.toml` pour les usages venv/pipx, tandis que `bb9.install` reste le parcours utilisateur qui gère aussi `~/.bb9/` et le `PATH`.

## 2026-05-23 — Demande de tool par protocole texte minimal

Décision : permettre au provider de demander une action avec le marqueur `BB9_ACTION`, sans appeler directement les tools.

Raison : l'agent doit pouvoir explorer un workspace au lieu de demander à l'utilisateur de coller des sorties de commandes, tout en gardant le guardian entre modèle et outils.

Conséquence : le kernel parse uniquement des demandes explicites comme `BB9_ACTION shell <commande>`. La loop exécute au plus quelques actions par tour via hooks, guardian et gateway, puis renvoie les observations au provider pour produire la réponse finale.

## 2026-05-23 — Budget de tools proche de l'expérience Codex

Décision : remplacer la limite basse fixe des tools par un budget profilé.

Raison : l'expérience attendue est qu'un agent puisse explorer réellement un projet, comme Codex le fait, sans s'arrêter après quelques lectures triviales.

Conséquence : le budget est plus large et dépend du profil `safe`, `limited` ou `power`. Il reste borné pour éviter les boucles infinies, mais il ne doit pas être présenté comme une excuse à l'utilisateur ; l'agent doit synthétiser avec les observations disponibles.

Amendement : le profil n'est pas seulement un budget technique. Il est aussi injecté dans le contexte provider comme niveau d'autonomie. En `power`, l'agent doit demander directement les actions utiles dans le workspace ou les trusted roots au lieu de demander à l'utilisateur s'il veut qu'une lecture soit faite.

## 2026-05-23 — Validation interactive des `ask`

Décision : la loop accepte un callback optionnel d'approbation et le REPL l'utilise pour traiter les verdicts guardian `ask`.

Raison : le guardian ne doit pas seulement refuser une action ambiguë ; il doit permettre au contrôle humain de débloquer un travail légitime, notamment sur un autre projet local.

Conséquence : en REPL, l'utilisateur peut refuser, autoriser une fois, ou ajouter un chemin hors workspace aux trusted roots. La loop reste indépendante du canal d'entrée et ne dépend pas directement du CLI.

## 2026-05-23 — Suppression du tool provisoire `echo`

Décision : retirer le tool Markdown `echo`.

Raison : `shell` est maintenant le premier vrai tool utile et contrôlé par le guardian. Garder `echo` entretiendrait une capacité de test sans usage produit.

Conséquence : `bb9/tools/INDEX.md` ne liste plus que les tools réels disponibles pour les agents.

## 2026-05-23 — Context-index Markdown minimal

Décision : générer un context-index Markdown dans le workspace courant, sous `.bb9/context-index.md`.

Raison : BB9 doit savoir rapidement où il se trouve dans le projet où il est lancé. Cette carte relève de sa mémoire de travail locale, régénérable, propre au workspace.

Conséquence : le context-index liste le périmètre, les fichiers de gouvernance, quelques dossiers et fichiers. Le kernel reçoit cette carte courte comme contexte préparé. Le fichier est écrit uniquement dans le workspace courant.

## 2026-05-23 — Secrets nommés via tool autonome

Décision : ajouter un store local de secrets nommés et un tool atomique `secret`.

Raison : BB9 doit pouvoir configurer des providers et outils sans demander à l'utilisateur de coller des secrets dans la conversation ou dans les fichiers du projet.

Conséquence : les secrets peuvent être référencés avec `secret:NOM`. Le provider résout cette référence seulement au moment nécessaire. Le modèle peut demander `BB9_ACTION secret add <NOM>`. Après validation `ask`, le REPL ouvre une capture de secret attendue : la prochaine saisie utilisateur est stockée localement et ne passe pas par le provider.

Amendement : le REPL intercepte aussi de façon opportuniste les entrées utilisateur qui ressemblent à des secrets avant l'appel provider. Le message brut est annulé, l'utilisateur peut stocker le secret localement, et la session ne garde qu'une trace redigée.

## 2026-05-23 — CalDAV comme tool local autonome

Décision : ajouter un tool atomique `caldav`.

Raison : lire un agenda est une capacité concrète et observable, mais savoir quand le lire, quoi en tirer et comment configurer les secrets relève d'une méthode.

Conséquence : `caldav` expose `doctor`, `agenda` et `maintenance` autour de `vdirsyncer` et `khal`. Sa méthode d'usage, ses secrets requis et son protocole sont déclarés dans `bb9/tools/caldav/TOOL.md`. Les secrets requis doivent passer par le tool `secret`.

Amendement : les implémentations concrètes des tools ne vivent pas dans `bb9/core/`. `shell`, `secret` et `caldav` sont autonomes dans leur archive sous `bb9/tools/<name>/`. Le core fournit seulement un chargeur générique de runtime pour éviter que le noyau absorbe les tools.

Amendement : quand une méthode d'usage appartient clairement à un tool, elle reste dans `bb9/tools/<name>/TOOL.md`. Les anciens doublons `skills/secret-management` et `skills/caldav-calendar` sont supprimés pour garder les tools comme archives autonomes.

Amendement : le store de secrets nommés, la détection locale d'entrée sensible et les commandes REPL associées vivent dans `bb9/tools/secret/`. Le CLI ne les importe pas directement : il charge l'entrée Python de l'archive via le chargeur générique et laisse le tool enregistrer ses extensions.

## 2026-05-23 — Extensions CLI déclarées par les tools

Décision : un tool natif ou un skill utilisateur peut fournir une entrée Python avec une fonction `register(cli)`. `core.py` est la forme cible ; `cli.py` reste accepté par compatibilité.

Raison : certains tools ont besoin d'une UX REPL propre : commandes slash, capture locale, validation interactive ou lignes de contexte. Ajouter ces cas un par un dans `bb9/core/cli.py` ferait grossir le noyau et casserait l'autonomie des tools.

Conséquence : le CLI expose un hôte générique. Les tools et skills peuvent enregistrer des commandes, intercepteurs d'entrée, handlers de validation et lignes de contexte. Le noyau garde seulement le mécanisme de découverte et d'orchestration.

Amendement 2026-05-26 : `cli.py` n'est pas le chemin par défaut pour exposer une capacité. Une capacité destinée à l'agent passe par `runtime.py` et `BB9_ACTION`. `cli.py` est réservé aux surfaces humaines ou système explicites : capture locale, validation, UI locale, commandes de contrôle. Une commande REPL ne doit pas court-circuiter l'agent ni retourner une observation technique brute comme réponse utilisateur.

## 2026-05-23 — Tool natif `create_skill`

Décision : ajouter un tool natif `create_skill` pour guider et scaffold les skills utilisateur.

Raison : BB9 doit aider l'agent à créer des extensions utilisateur portables sans transformer cette logique en connaissance implicite du noyau.

Conséquence : `bb9/tools/create_skill/TOOL.md` rassemble les règles de création de skills. `BB9_ACTION create_skill draft <nom>` crée un squelette `SKILL.md` dans `~/.bb9/skills/`, et `BB9_ACTION create_skill draft <nom> core` ajoute un squelette `core.py`. `cli` reste accepté comme alias historique.

## 2026-05-23 — Tools natifs, skills utilisateur

Décision : un tool ou un skill est une archive Markdown autonome, avec backend optionnel. Les deux peuvent ajouter une capacité ou un comportement attendu.

Raison : la frontière capacité/méthode crée trop de doublons. Ce qui compte pour l'utilisateur est de savoir si la brique est livrée avec BB9 ou ajoutée localement.

Conséquence : les tools vivent dans l'archive BB9 sous `bb9/tools/<name>/TOOL.md`. Les skills vivent dans `~/.bb9/skills/<name>/SKILL.md`. Les deux doivent rester portables, copiables, sans chemin local en dur. `project-explorer` et `project-onboarding` sont donc des tools documentaires natifs.

## 2026-05-23 — Stabilisation des frontières locales

Décision : protéger automatiquement la mémoire `.bb9/` des workspaces avec un `.gitignore`, et traiter les extensions `cli.py` des skills comme du code local de confiance.

Raison : `.bb9/context-index.md` est utile dans le workspace mais ne doit pas être versionné par accident. À l'inverse, `~/.bb9/skills/<name>/cli.py` peut exécuter du code au démarrage du REPL ; il doit être assumé comme une extension locale relue, pas comme un simple Markdown inerte.

Conséquence : BB9 crée `.bb9/.gitignore` dans le workspace quand il génère le context-index. Les docs indiquent qu'un skill peut orienter l'agent vers des tools, enregistrer une extension REPL via `cli.py` ou exposer une action contrôlée via `runtime.py`.

## 2026-05-23 — Goal loop persistante

Décision : ajouter `/goal` comme brique d'orchestration runtime, au-dessus de `run_once`, avec état persistant dans `~/.bb9/goals/active.json`.

Raison : un objectif autonome doit modifier le modèle d'exécution. Il ne suffit pas de stocker du texte : BB9 doit boucler, agir, vérifier et s'arrêter seulement quand des conditions de succès sont prouvées.

Conséquence : `bb9.core.goals` porte `GoalManager`, `GoalLoopRunner` et `EvaluatorAgent`. Le CLI route `/goal` vers cette brique. Les actions continuent de passer par kernel, loop, hooks, guardian, gateway et tools. Le succès exige des vérifications concrètes ; sans vérification exploitable, le goal est mis en pause ou continue, mais n'est pas marqué atteint.

## 2026-05-25 — Ambition fonctionnelle complète, complexité déplacée dans Markdown

Décision : BB9 n'est pas moins ambitieux que Marius fonctionnellement. Il est plus strict sur l'emplacement de la complexité : le kernel exécute des contrats courts ; le Markdown porte l'intention, la configuration, les comportements, les politiques et les workflows ; les interfaces restent remplaçables.

Raison : `minimal` ne doit pas vouloir dire moins de capacités. BB9 vise toujours un agent local puissant avec agents, subagents, tools, skills, sessions, gateway, cron, dreaming, mémoire métier et persistance. La différence est que ces briques doivent d'abord être lisibles, découvrables et configurables par fichiers Markdown, au lieu d'être figées dans beaucoup de code Python spécifique.

Conséquence : une nouvelle capacité BB9 doit d'abord être modélisée comme une archive Markdown découvrable. Le Python n'intervient que pour fournir un chargeur, un validateur, un runner générique, un adapter d'exécution ou une frontière de sécurité. Si une feature exige beaucoup de Python spécifique, c'est un signal qu'elle n'est pas encore assez bien découpée.

Conséquence : un dashboard, une app desktop ou toute autre interface peut exister plus tard, mais seulement comme client externe branché au runtime ou au gateway. Aucune interface ne doit devenir la source de vérité du kernel, des agents, des skills, des tools, des routines, des dreams ou des sessions.

## 2026-05-25 — Archives Markdown d'abord

Décision : toute brique agentique durable doit être représentable comme une archive Markdown lisible, copiable, indexable et désactivable.

Raison : BB9 doit rester simple à paramétrer et à auditer. L'utilisateur doit pouvoir comprendre et modifier les agents, skills, tools, cron, dreams et politiques sans parcourir une architecture Python profonde.

Conséquence : les agents, subagents, skills, tools, routines cron, dreams et workflows doivent converger vers une forme commune d'archive : un dossier nommé, un fichier Markdown principal, des fichiers Markdown optionnels et seulement si nécessaire un backend local borné. Le runtime Python charge ces archives, résout l'héritage, applique les désactivations, construit les index et délègue l'exécution au guardian puis au gateway.

Conséquence : les fichiers Python associés à une archive restent des backends optionnels et locaux. Ils ne doivent pas devenir la source principale de configuration, de politique ou de workflow. Les choix durables vivent en Markdown ; le code fournit le mécanisme.

Amendement 2026-05-26 : un skill et un tool peuvent tous les deux agir ou définir un comportement. La frontière principale devient leur lieu et leur statut : les tools sont natifs et livrés dans `bb9/tools/`, les skills sont utilisateur, autonomes et partageables dans `~/.bb9/skills/`. Une archive skill/tool contient au minimum `SKILL.md` ou `TOOL.md`, peut fournir `DREAM.md`, et peut fournir du Python optionnel. `runtime.py` est la porte d'entrée action, `cli.py` la porte d'entrée REPL, et `core.py` ou `core/core.py` un backend partagé si besoin.

## 2026-05-26 — `/plan`, `/dev` et tâches subagents standalone

Décision : BB9 utilisera deux skills de méthode pour préparer la délégation : `/plan` pour découper une demande en tâches, dépendances et parallélisation possible ; `/dev` pour exécuter ce plan en respectant les dépendances et en lançant les tâches parallélisables quand c'est sûr.

Raison : un subagent ne doit pas recevoir un morceau flou du problème. Le parent doit lui mâcher une tâche autonome, comme une user story standalone, avec objectif explicite, contexte suffisant, contraintes, droits, résultat attendu et critères de done.

Conséquence : la future délégation runtime reste un contrat court `delegate(task, subagent) -> TaskResult`. Le subagent retourne `done` ou `error` avec résumé, preuves et bloqueurs. Le parent garde la trace canonique dans le chat utilisateur : lancements, retours, erreurs et conséquence sur le plan.

Amendement : la première implémentation runtime est synchrone et découplée du CLI : `delegate(task, subagent, parent_context, runner) -> TaskResult`. Elle valide le contrat minimal de la tâche, construit un contexte réduit, empêche la délégation récursive libre en retirant l'index des subagents, plafonne le profil de permission au parent et convertit l'observation en `TaskResult`.

Amendement : le template skill `/dev` fournit un premier branchement explicite `/dev delegate ...`. Il parse une tâche standalone en champs `id`, `worker`, `goal`, `context`, `expected`, puis appelle le runtime de délégation. Les autres usages de `/dev` restent envoyés au skill Markdown.

Amendement : `/plan` écrit le plan courant dans `.bb9/plan.md` et écrase l'ancien plan. `/dev` lit ce fichier sans argument, parse les tâches sous forme de cases Markdown (`- [ ] T1 ...`) et exécute les tâches séquentiellement via le runtime de délégation. Les dépendances échouées bloquent les tâches dépendantes. Les tâches réussies sont cochées dans `.bb9/plan.md`. Le parallélisme reste volontairement différé.

Amendement : `/dev` peut maintenant lancer une vague parallèle uniquement pour des tâches prêtes, marquées `parallelizable: true`, avec un champ `paths:` non vide et sans intersection de chemins avec les autres tâches de la vague. Les tâches sans `paths:` ou avec conflit évident restent séquentielles.

Amendement : `/dev` garde les ids de tâches comme ancres internes au Markdown, mais la trace conversationnelle et le récap final utilisent les titres humains. Après chaque tâche exécutée, `/dev` écrit un état court dans `.bb9/plan.md` (`status`, `summary`, et si besoin `blockers` ou `evidence`) pour permettre la reprise sans transformer le plan en log complet.

Amendement : `/plan` et `/dev` sont fournis comme templates de skills utilisateur installés si absents. Une commande slash inconnue qui correspond au nom d'un skill actif est routée comme intention vers le kernel, ce qui rend ces méthodes utilisables sans `cli.py` dédié.

Amendement : un skill peut être global (`~/.bb9/skills/`) ou local au workspace (`.bb9/skills/`). À nom égal, le skill local prend le dessus. Les commandes d'un skill ou d'un tool appartiennent à son archive : elles sont déclarées dans le Markdown et enregistrées par `cli.py` seulement si une intégration REPL réelle est nécessaire.

Amendement : les collisions de commandes d'archives sont visibles et non silencieuses. Une commande native du REPL gagne toujours. Si plusieurs archives actives déclarent la même commande, ou si une archive déclare une commande native, BB9 le signale dans le contexte et ne route pas automatiquement cette commande d'archive.

## 2026-05-25 — Cron unifié pour tâches planifiées et routines

Décision : BB9 utilise une seule archive `CRON.md` pour les intentions différées et récurrentes. Une tâche planifiée unitaire et une routine récurrente ont la même forme, avec `Mode: once` ou `Mode: recurring`.

Raison : un cron planifié et un cron récurrent partagent la même nature : déclencher une intention explicite à un moment défini. Les séparer trop tôt multiplierait les concepts et le code alors que seule la politique après exécution change.

Conséquence : `once` utilise une date et une heure (`At`). `recurring` utilise une heure (`Time`) et peut préciser des jours (`Days`) comme `daily`, `weekdays`, `weekend` ou une liste de jours. Après exécution, un cron `once` peut être archivé, supprimé ou mis en pause ; un cron `recurring` reste actif sauf erreur ou politique contraire.

Conséquence : `CRON.md` reste la source déclarative. L'état calculé (`last_run`, `next_run`, erreurs, locks, historique) vit dans la persistance runtime, pas dans l'archive Markdown.

Amendement : le premier runner cron est une couche pure de calcul `due/next_run`. Il ne lance pas encore d'agent et n'écrit pas l'historique. Pour les routines récurrentes, il déclenche seulement l'occurrence du jour courant et ne rattrape pas automatiquement une occurrence ancienne manquée.

Amendement : le branchement runtime initial vit dans la commande `/cron`. `/cron tick` reste explicite et passe par la loop normale plutôt que d'exécuter une action directement depuis le scheduler. L'état technique minimal vit dans `~/.bb9/cron-state.json`, séparé des archives `CRON.md`.

Amendement : `Retry`, `Notification` et `History` sont des politiques déclarées dans `CRON.md`, puis interprétées par le runtime. Le scheduler calcule et applique ces politiques minimales, mais les transports de notification, l'affichage avancé d'historique et les stratégies plus fines restent des adapters branchés autour.

## 2026-05-25 — DREAM.md comme contrat de contribution au dreaming

Décision : `DREAM.md` ne définit pas une cadence et ne remplace pas `CRON.md`. Un `DREAM.md` dans un skill ou un tool décrit la valeur que cette brique apporte au moteur dreaming : signaux, sources, actions proposées et garde-fous. Une archive `~/.bb9/dreams/<name>/DREAM.md` décrit un cycle de consolidation, mais son déclenchement reste explicite ou planifié par `CRON.md`.

Raison : le dreaming est une fonction de consolidation qui croise memory, sessions, mémoire projet et données déclarées par les skills/tools. Le traiter comme un cron spécial recréerait un scheduler parallèle et mélangerait `quand lancer` avec `quoi consolider`.

Conséquence : le runner dreaming charge les contrats Markdown, construit un contexte, prépare un prompt de consolidation, parse des opérations JSON et les applique à la mémoire SQL graph. Les actions métier produites par le dreaming restent `proposed` et ne sont pas exécutées automatiquement.

Amendement 2026-05-25 : `/dream` est la commande explicite du moteur de consolidation. Elle peut lister les archives, inspecter le contexte, afficher le prompt ou lancer un run provider. Même en run, le dreaming applique seulement les opérations mémoire SQL graph retournées ; les actions restent proposées.

Amendement 2026-05-25 : la validation humaine du dreaming est optionnelle via `/dream preview` puis `/dream apply`. Le plan pending vit dans `~/.bb9/dream-pending.json`, comme état runtime temporaire. Les routines peuvent aussi lancer `/dream run <name>` depuis une section `Command` de `CRON.md`, ce qui garde la cadence dans le cron et la consolidation dans le dream.

## 2026-05-26 — Historique visible et artefacts séparés de la session courte

Décision : BB9 distingue désormais la session courte, la trace runtime, la mémoire durable et l'historique visible utilisateur. L'historique visible vit dans `~/.bb9/visible-history.db` et garde les messages relisibles ainsi que les artefacts référencés.

Raison : Marius montre qu'un agent utile dans le temps doit pouvoir relire un fil visible, rattacher des rapports, diffs, screenshots ou fichiers, et alimenter de futures surfaces sans confondre cela avec le contexte compactable envoyé au provider.

Conséquence : le CLI écrit chaque tour utilisateur/assistant dans `sessions.db` pour le contexte court et dans `visible-history.db` pour le fil visible. La commande `/history` exporte ce fil en Markdown portable. Les artefacts restent des références structurées (`diff`, `image`, `report`, `file`, `screenshot`, `note`) ; ils n'imposent ni dashboard ni daemon.

Amendement : un artefact `diff` est rattaché au tour qui a produit les modifications. Son rendu cible est une carte de revue pliée par défaut : résumé global, compteurs `+/-`, fichiers touchés, puis expansion fichier par fichier. Les channels moins riches gardent le même service via Markdown, fichier `.diff` ou lien d'artefact.

Amendement : un artefact `tool_trace` est rattaché au tour quand des tools ont été exécutés. Il garde le nom du tool, son statut et un résumé court. Il ne remplace pas le bilan naturel de l'agent et ne stocke pas l'observation brute complète.

## 2026-05-26 — Rapports de dream persistés

Décision : chaque `/dream run` et chaque `/dream apply` produit un rapport JSON et Markdown dans `~/.bb9/dreams/reports/`.

Raison : le dreaming modifie la mémoire durable et propose des suites utiles. Il faut donc garder une preuve relisible du cycle sans transformer cette preuve en mémoire elle-même.

Conséquence : les rapports listent le dream ciblé, le mode (`run` ou `apply`), le projet courant, les opérations parsées, les actions proposées, les compteurs d'application mémoire, les erreurs et le résumé. Le Markdown du rapport est attaché à l'historique visible comme artefact `report`. Les commandes `/dream reports` et `/dream report <id>` permettent de les relire sans dashboard.

## 2026-05-26 — Persistance métier minimale `tasks`

Décision : BB9 ajoute un store métier minimal pour les tâches durables, exposé par le tool natif `tasks`.

Raison : Marius tient le travail dans le temps avec un task board et des stores métier. BB9 doit reprendre cette capacité sans déplacer les workflows dans le kernel ni confondre cron, plan et mémoire.

Conséquence : le contrat d'usage vit dans `bb9/tools/tasks/TOOL.md` et `docs/tasks.md`. L'état runtime vit dans `~/.bb9/tasks/tasks.json`. Le tool permet `create`, `list` et `update` ; les écritures demandent confirmation. Une tâche peut avoir une échéance métier `scheduled_for`, mais l'exécution automatique reste du ressort de `CRON.md` ou d'un mode continu explicite.

Amendement : le dreaming peut produire une action `task.create`. Cette action est matérialisée en tâche durable seulement pendant `/dream run` ou `/dream apply`, jamais pendant `/dream preview`. Elle n'exécute pas la tâche, ne lance pas d'agent et ne crée pas de cron ; elle transforme seulement une suite utile en état métier persistant.

Amendement : `tasks` reste un tool pour l'agent, pas une commande REPL utilisateur. L'utilisateur demande en langage naturel ; l'agent décide d'appeler `BB9_ACTION tasks ...` si c'est utile, puis répond avec un bilan naturel. La sortie brute du tool est une observation technique pour l'agent et ne doit pas court-circuiter la réponse finale.

## 2026-05-26 — Alignement des surfaces

Décision : CLI, chat web, Telegram, dashboard éventuel et futurs channels doivent préserver le même service fonctionnel autant que leur transport le permet.

Raison : BB9 ne doit pas devenir une somme de surfaces divergentes. Une feature comme les traces, artefacts, confirmations, listes Markdown, commandes ou notifications doit garder le même contrat, même si son rendu varie selon le canal.

Conséquence : le service commun vit dans les contrats Markdown, la loop, les stores et les archives. Les channels adaptent seulement l'entrée, le rendu, les confirmations et les contraintes du transport. Si un canal ne peut pas rendre une feature complète, il doit fournir une dégradation explicite : résumé, lien, fichier, artefact ou message clair.

Conséquence : une commande REPL n'est pas le service lui-même ; c'est une syntaxe locale du CLI. Le chat web ou Telegram peuvent exposer la même capacité par texte naturel, bouton, slash command, menu ou Markdown, sans changer le contrat.

Amendement : l'activité de l'agent doit être visible. Une surface doit distinguer l'agent actif, un tool en cours (`live_tool_use`) et un tool terminé (`tool_trace`). Une longue attente silencieuse est un défaut d'UX, même si le runtime travaille correctement.

Amendement : la loop peut transmettre ses événements au channel pendant le tour. Le premier branchement concret est le CLI, qui affiche un marqueur quand un tool démarre puis un statut `ok` ou `error` quand l'observation revient.

Amendement : une demande d'analyse de repo, projet ou dossier appelle une synthèse, pas un inventaire. L'agent doit donner la nature du projet, son verdict, les risques et les priorités d'amélioration. Les fichiers, APIs ou méthodes ne sont cités que pour soutenir une conclusion, sauf demande explicite de structure.

Amendement : le CLI rend un sous-ensemble léger de Markdown quand le terminal supporte ANSI, et conserve le Markdown brut en sortie non interactive. Le rendu améliore la lisibilité des réponses, historiques et rapports sans introduire de dépendance UI lourde.

Amendement : les messages utilisateur sont traités comme des ancres visuelles dans le CLI, mais ne sont pas recopiés après le prompt. La mise en valeur passe par l'espacement du tour et reste purement présentationnelle : elle ne modifie ni la session persistée, ni l'intention envoyée au kernel.

Amendement : les blocs de code du CLI peuvent recevoir une coloration syntaxique légère pour les langages courants (`js`, `ts`, `json`, `python`, `bash`). Cette coloration reste opportuniste, sans parser complet ni dépendance lourde.

Amendement : le CLI affiche un point de focus animé pendant que l'agent prépare une réponse ou attend un tool. Cet indicateur est éphémère, se nettoie avant les sorties persistantes, se suspend pendant les validations humaines et ne devient ni trace, ni message, ni contexte provider.

Amendement : les traces visibles de tools distinguent le live et le terminé. Pour `shell`, la commande demandée peut être affichée en bloc `bash` et stockée dans l'artefact `tool_trace`, mais la sortie brute du tool reste une observation pour l'agent ; la réponse utilisateur reste un bilan naturel.

Amendement : un échec structurel de tool, comme `browser` sans Playwright disponible, rend le tool indisponible pour le reste du tour. La loop force ensuite une réponse finale avec les observations disponibles au lieu de relancer le même tool plusieurs fois. Les traces live résument les sorties longues ou HTML au lieu d'afficher le brut.

Amendement : l'anti-boucle de la loop ne doit pas bloquer un retry rendu valide par une action intermédiaire. Exemple : `browser screenshot` peut échouer avec "No page open", puis devenir valide après `browser open`. Ce type d'échec de précondition n'est pas marqué comme action définitivement ratée.

Amendement : un échec de navigation `browser` sur une URL locale peut être récupérable si le serveur accepte la connexion mais ne répond pas correctement (`ERR_EMPTY_RESPONSE`, connexion refusée ou reset). Dans ce cas, la loop bloque seulement le retry exact et laisse une étape pour une action différente qui change la situation, par exemple démarrer un serveur HTTP local et utiliser l'URL réellement retournée.

Amendement : l'index des tools peut porter un statut de disponibilité runtime. Pour `browser`, l'absence du package Python Playwright est indiquée comme `unavailable` dans le contexte provider. L'agent doit répondre depuis ce statut au lieu d'appeler le tool pour découvrir qu'il manque.

Amendement : `browser` doit fonctionner depuis toutes les surfaces, y compris celles qui tournent déjà dans une boucle asyncio. Marius utilise Playwright Sync API dans un chemin synchrone ; BB9, lui, peut appeler le même tool depuis un CLI ou un channel async. Pour éviter les faux négatifs de détection et garder les sessions persistantes cohérentes, toutes les opérations Playwright de BB9 passent donc par un thread navigateur dédié.

Amendement : les modules Python de tools et skills sont rechargés si leur code source change (`runtime.py`, `core.py` ou helper local). Une session BB9 longue ne doit pas garder indéfiniment un ancien backend en mémoire après une correction locale.

Amendement : après un tour CLI qui modifie le worktree, BB9 affiche un résumé compact du diff : nombre de fichiers, compteurs `+/-` et fichiers touchés. Le patch complet reste dans l'artefact `diff` et dans `/history`; il n'est pas déroulé par défaut dans la conversation.

Amendement : le wizard `/model` accepte une référence de secret (`env:`, `file:`, `secret:`), un nom d'environnement, ou une clé brute. Une clé brute est immédiatement stockée dans le store secret local et remplacée par une référence `secret:...` avant la récupération des modèles. Le wizard tente ensuite de lister les modèles et ne réaffiche jamais la valeur brute.

Amendement : le premier chat web est un channel local HTTP en bibliothèque standard, pas un dashboard. Il sert `127.0.0.1`, expose `/api/chat`, réutilise `intention_from_text`, `build_context`, `run_once`, la session courte et l'historique visible avec `source=web`. La première version retourne les événements après le tour ; le streaming et les validations guardian web restent des raffinements futurs.

Amendement : l'API chat et l'interface web sont séparées. `bb9/api/` contient le service réutilisable et le transport HTTP JSON (`/api/chat`, `/api/history`, `/health`). `bb9/chat-web/` contient le client statique. La commande `bb9 web` compose directement ces deux briques, afin qu'une autre app puisse plus tard consommer la même API sans dépendre du client chat.

Amendement : le chat web distingue le projet actif de surface et le workspace d'exécution. Le projet actif filtre les sessions et l'historique visible exposés par l'API, mais les actions runtime restent limitées au workspace du processus `bb9 web` tant qu'un mécanisme explicite de switching runtime n'existe pas.

Amendement : le chat web découvre les commandes slash natives et les commandes d'archives du projet actif via `/api/commands`, puis les expose en autocomplétion dans le composer. Il découvre aussi les thèmes web via `/api/themes`; les thèmes personnalisés sont des fichiers CSS dans `.bb9/themes/web`, `~/.bb9/themes/web` ou `bb9/chat-web/themes`.

Amendement : le chat web peut demander l'arrêt du run courant via `/api/stop`. L'arrêt est coopératif : il est vérifié entre les décisions et les actions, sans prétendre interrompre instantanément un appel provider ou un effet de bord déjà lancé. Pendant un run, le composer reste éditable et les messages soumis sont gardés dans une queue locale modifiable jusqu'à leur envoi.

Amendement : Ollama local et Ollama Cloud sont deux providers distincts. Ollama local utilise l'endpoint OpenAI-compatible `http://localhost:11434/v1`, sans clé API. Ollama Cloud utilise `https://ollama.com`, une clé `OLLAMA_API_KEY`, `/api/tags` pour lister les modèles et `/api/chat` pour générer. `https://ollama.com` ne doit donc pas être normalisé vers localhost.
