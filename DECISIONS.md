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

Amendement 2026-06-12 : BB9 distingue maintenant le channel conversationnel de
la session persistée. Chaque agent possède automatiquement un channel d'accueil
sans path projet. Les routines, Telegram et les notifications globales d'un
agent doivent écrire dans cet accueil d'agent. Les sessions projet restent liées
à un workspace/path, et le lancement de BB9 depuis un workspace reste
workspace-first.

Amendement 2026-06-13 : l'accueil d'un agent est une session canonique unique
(`agent-home:<agent>`), pas une pile de sessions. Les surfaces qui proposent
"nouvelle session" doivent réserver cette action aux sessions projet et ne pas
créer de second accueil avec un id arbitraire.

Amendement 2026-06-12 : la configuration Telegram appartient à l'agent, pas aux
tools. Elle vit dans `TELEGRAM.md` avec une référence de secret pour le token et
les chat IDs autorisés ; le transport Telegram reste un host/channel externe qui
lira cette config.

Amendement 2026-06-12 : le transport Telegram est un host explicite lancé par
`bb9 telegram`. Il poll Telegram, filtre les chats autorisés, route les messages
vers l'accueil de l'agent et persiste l'offset localement.

Amendement 2026-06-12 : Telegram peut confirmer les validations guardian
sensibles via clavier inline. Le host envoie un message `Valider` / `Refuser`,
attend la `callback_query`, répond avec `answerCallbackQuery`, puis reprend le
tour avec `allow` ou `deny`. Telegram expose aussi le REPL par commandes bot.
La déclaration native des commandes est partagée avec le web ;
`/help` Telegram liste le même ensemble BB9, tandis que `setMyCommands` ne
publie que les commandes dont le nom respecte les contraintes Telegram.

Amendement 2026-06-12 : `bb9 web` gère aussi le cycle de vie Telegram de l'agent
actif. Au démarrage du web, une config Telegram active lance le host en fond ; si
l'utilisateur active ou désactive Telegram depuis la modale agent, le host est
resynchronisé sans commande terminal supplémentaire.

## 2026-05-22 — Workspace comme frontière locale

Décision : le workspace est la frontière locale par défaut pour les lectures, écritures et commandes d'une tâche agentique.

Raison : les outils récents isolent les runs dans des workspaces pour limiter les effets de bord, comparer les résultats et demander confirmation avant de sortir du périmètre.

Conséquence : la phase 1 peut utiliser le dépôt courant comme workspace simple. Les worktrees, agents parallèles, scripts setup/run/teardown et automations restent des options futures.

Amendement 2026-06-12 : le changement de workspace est une primitive coeur,
pas une fonction de Telegram. Un channel peut résoudre une demande comme
`mets-toi sur le projet <nom>` vers un path connu ou proche, puis exécuter la
suite de la demande dans ce workspace sans changer la session conversationnelle
quand le channel a une destination canonique. BB9 signale aussi les lancements
depuis un workspace trop large, comme le dossier utilisateur ou la racine
système, pour encourager un cadrage projet explicite.

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

Amendement remplacé le 2026-06-12 : `/goal` n'est plus représenté par un subagent `goal`.

Amendement 2026-06-13 : `MODEL.md` permet a un agent ou subagent de definir son provider et son modele effectifs via `ProviderId` et `Model`, en reutilisant l'entrée provider déclarée et ses secrets.

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

Amendement : `find ... | sort` est un pipeline de lecture sûr reconnu par le runtime shell. Il est exécuté comme deux processus chaînés, sans `shell=True`; un pipeline non supporté ne doit pas être passé tel quel à la commande de gauche après validation.

Amendement : `python3 -m http.server <port>` est une commande longue reconnue de prévisualisation locale. En `limited` et `power`, elle peut démarrer en arrière-plan dans le workspace sans ask, avec bind forcé à `127.0.0.1` si absent. Ce n'est pas une commande de test courte et elle ne doit pas finir en timeout.

Amendement : les chaînes `&&` composées uniquement de commandes de lecture sûres peuvent être autorisées sans validation en `limited` et `power`, tout en restant exécutées sans `shell=True`. Le runtime les découpe et les lance séquentiellement en argv; les chaînes contenant écriture, redirection, `||`, `;` ou commande inconnue restent soumises au guardian.

Amendement : les pipelines de lecture composés uniquement de commandes allowlistées (`find`, `grep`, `rg`, `sort`, `head`, etc.) peuvent être exécutés sans `shell=True` par chaînage de processus. Un pipeline non supporté est bloqué avant validation humaine afin d'éviter une UX où l'utilisateur autorise une commande que le runtime refusera ensuite.

Amendement : le tool `shell` exécute ses sous-processus avec le workspace du `RunContext` comme `cwd`, pas avec le dossier courant accidentel du processus Python. Les commandes de lecture allowlistées restent refusées si elles portent des options mutantes comme `sed -i`, `find -delete/-exec` ou `sort -o`.

Amendement : les commandes shell doivent être classées par familles compréhensibles plutôt que tomber trop vite dans `unknown`. La première famille ajoutée est l'interpréteur Python local via heredoc (`python3 - <<'PY' ... PY` ou `python - <<'PY' ... PY`), autorisé en `limited` et `power` dans le workspace et exécuté via stdin sans `shell=True`.

Amendement : le tool `shell` doit réduire les faux blocages en classant les formes courantes avant de décider. Les chaînes `&&` sont découpées en argv et classées par familles (`read`, `verification`, `workspace_write`, `destructive`, `unknown`) ; les redirections simples de stdout vers fichier sont traitées comme des écritures contrôlées. Les vraies syntaxes non supportées sans `shell=True` restent bloquées, mais les commandes destructives ou inconnues bien parsées demandent validation plutôt que de devenir des stops techniques.

Amendement : les tools qui manipulent le workspace, notamment `files` et `browser`, doivent eux aussi exécuter leurs effets et artefacts relativement au workspace du `RunContext`. Le cwd du processus Python ne doit pas devenir une source implicite de vérité.

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

Amendement : un `BB9_ACTION` imbriqué dans le corps d'une autre action provider est une action malformée. Le kernel la transforme en `invalid-provider-action` bloquée automatiquement, au lieu de laisser le fragment imbriqué atteindre un tool comme argument shell et déclencher une validation humaine inutile.

Amendement : une prose de réponse finale collée aux paramètres d'un tool ne doit pas rendre l'action exécutable. Les tools à protocole strict comme `browser` refusent les tokens positionnels inattendus ou booléens invalides, et `shell` bloque une commande `python3 -m http.server` dont le port contient du texte avant toute validation humaine.

Amendement : le tool `files` accepte aussi une action JSON naturelle de forme `{ "ops": [{ "op": "write", "path": "...", "content": "..." }] }`, normalisée en `write_many`. Ce format reste borné aux écritures explicites et passe par le même guardian que les autres opérations `files`.

## 2026-05-23 — CLI interactif sans dépendance externe

Décision : le premier CLI interactif vit dans `bb9/core/cli.py` et utilise seulement la bibliothèque standard.

Raison : le système doit être agréable à utiliser, mais rester minimal et portable.

Conséquence : `python3 -m bb9` sans intention ouvre un REPL avec commandes slash. `python3 -m bb9.cli` lance le même mode interactif. Le REPL expose seulement les commandes utiles à l'utilisateur ; les outils et réglages internes restent pilotés par le runtime ou par options de lancement.

## 2026-05-23 — Index Markdown générés pour skills et tools

Décision : `~/.bb9/skills/INDEX.md` et `bb9/tools/INDEX.md` sont générés depuis les fichiers sources.

Raison : une liste maintenue à la main dériverait rapidement. Le kernel a besoin d'un contexte court sans injecter tous les fichiers complets.

Conséquence : les indexes résument les skills utilisateur et tools natifs actifs. Ils sont régénérés au lancement de `bb9`. Les skills `always` peuvent être injectés en complet ; les tools restent résumés par défaut.

Amendement : l'index de skills utilise `## Résumé`, puis `description:` en fallback, afin de fournir une carte claire au modèle sans charger tous les corps de skills. Un skill `on-demand` peut aussi déclarer des déclencheurs `activation:` qui chargent son corps pour une intention donnée sans créer de commande REPL routable ni collision d'autocomplete.

## 2026-05-23 — Provider config reprise de Marius, mais réduite

Décision : reprendre la logique Marius de configuration provider sous une forme minimale : registre, config locale, references de secrets, recuperation des modeles et assistant `/model`.

Raison : le choix provider/auth/modele est une vraie brique utilisateur. Il doit fonctionner pour les API keys, mais aussi laisser une place explicite aux auth web type ChatGPT/Codex.

Conséquence : `bb9.core.provider_config` contient cette brique sans dependance externe. La config provider stocke le provider actif et des references de secrets, pas des secrets bruts. L'auth web ChatGPT/Codex est portee depuis Marius sous forme experimentale : tokens locaux dans `~/.bb9/secrets/`, adapter runtime dedie, et fallback API key/OpenRouter recommande si le flux web change.

Amendement : la config provider par defaut devient utilisateur (`~/.bb9/providers.json`) afin que BB9 fonctionne depuis n'importe quel workspace apres installation editable. `.bb9/providers.json` ne doit plus être choisi automatiquement ; une surcharge doit être explicite via option ou variable d'environnement.

Amendement 2026-06-13 : les surfaces doivent afficher le couple provider/modele
effectif du run courant. Changer d'agent peut changer ce couple ; l'ancien
provider global ne doit pas rester visible comme provider actif du run.

## 2026-05-23 — Historique court de session dans le contexte provider

Décision : la session CLI conserve un historique court et borné des tours utilisateur/assistant, injecté au provider par le kernel.

Raison : un agent conversationnel inutilisable sans continuité forcerait l'utilisateur à répéter le contexte. Cette continuité doit rester temporaire et séparée de la memory durable.

Conséquence : `Session` porte des messages récents en mémoire. `/new` repart sur une session vide. Le kernel lit ce contexte mais ne le persiste pas et ne l'écrit pas dans `MEMORY.md`.

Amendement : la session peut être compactée. `/compact` force une compaction locale du contexte court, et une auto-compaction se déclenche quand la session devient trop longue. La compaction produit un résumé dérivé interne, conserve les messages récents et ne modifie pas la mémoire durable.

Amendement : l'auto-compaction s'appuie sur une resolution automatique des metadonnees de modele, mais sans requete web implicite. BB9 garde un cache dans `~/.bb9/model-metadata.json`, utilise une table connue embarquee, puis un fallback prudent. Les seuils de fenetre sont 90% pour trim, 95% pour summarization et 98% pour reset ; une limite souple d'entree explicite du provider peut encore declencher une synthese plus tot. Une mise a jour web devra passer par une commande ou un tool explicite.

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

Amendement : les conflits de chemins de `/build` ne sont pas seulement des égalités exactes. Un chemin parent et un chemin enfant, par exemple `docs` et `docs/skills.md`, se chevauchent et ne doivent pas être lancés dans la même vague parallèle.

Amendement : `/dev` garde les ids de tâches comme ancres internes au Markdown, mais la trace conversationnelle et le récap final utilisent les titres humains. Après chaque tâche exécutée, `/dev` écrit un état court dans `.bb9/plan.md` (`status`, `summary`, et si besoin `blockers` ou `evidence`) pour permettre la reprise sans transformer le plan en log complet.

Amendement : `/plan` et `/dev` sont fournis comme templates de skills utilisateur installés si absents. Une commande slash inconnue qui correspond au nom d'un skill actif est routée comme intention vers le kernel, ce qui rend ces méthodes utilisables sans `cli.py` dédié.

Amendement : un skill peut être global (`~/.bb9/skills/`) ou local au workspace (`.bb9/skills/`). À nom égal, le skill local prend le dessus. Les commandes d'un skill ou d'un tool appartiennent à son archive : elles sont déclarées dans le Markdown et enregistrées par `cli.py` seulement si une intégration REPL réelle est nécessaire.

Amendement : les collisions de commandes d'archives sont visibles et non silencieuses. Une commande native du REPL gagne toujours. Si plusieurs archives actives déclarent la même commande, ou si une archive déclare une commande native, BB9 le signale dans le contexte et ne route pas automatiquement cette commande d'archive.

Amendement 2026-05-31 : la commande publique d'exécution du plan devient `/build`. Le skill historique reste dans l'archive `dev` pour éviter une migration de dossiers utilisateur, mais ses templates, sa commande REPL et sa documentation exposent `/build` et `/build delegate`.

Amendement : certaines commandes de skills locaux peuvent exprimer une livraison attendue dans le workspace via une section `Contrat de livraison` de type `workspace-artifact`. La loop ne termine pas sur une réponse textuelle seule avant une tentative `files`, sauf question de clarification courte. Cela garde le skill actionnable sans transformer chaque livraison en tool dédié ni coder le nom du skill dans BB9.

Amendement 2026-06-06 : BB9 a un réflexe plan léger pour les demandes clairement multi-étapes. Le kernel l'indique au provider, le skill `plan` expose des déclencheurs d'activation ciblés, et le chat web peut lancer automatiquement `/plan` pour une demande naturelle complexe quand aucun plan courant n'existe. Ce réflexe prépare le plan seulement ; `/build` reste une commande explicite de l'utilisateur.

## 2026-05-25 — Cron unifié pour tâches planifiées et routines

Décision : BB9 utilise une seule archive `CRON.md` pour les intentions différées et récurrentes. Une tâche planifiée unitaire et une routine récurrente ont la même forme, avec `Mode: once` ou `Mode: recurring`.

Raison : un cron planifié et un cron récurrent partagent la même nature : déclencher une intention explicite à un moment défini. Les séparer trop tôt multiplierait les concepts et le code alors que seule la politique après exécution change.

Conséquence : `once` utilise une date et une heure (`At`). `recurring` utilise une heure (`Time`) et peut préciser des jours (`Days`) comme `daily`, `weekdays`, `weekend` ou une liste de jours. Après exécution, un cron `once` peut être archivé, supprimé ou mis en pause ; un cron `recurring` reste actif sauf erreur ou politique contraire.

Conséquence : `CRON.md` reste la source déclarative. L'état calculé (`last_run`, `next_run`, erreurs, locks, historique) vit dans la persistance runtime, pas dans l'archive Markdown.

Amendement : le premier runner cron est une couche pure de calcul `due/next_run`. Il ne lance pas encore d'agent et n'écrit pas l'historique. Pour les routines récurrentes, il déclenche seulement l'occurrence du jour courant et ne rattrape pas automatiquement une occurrence ancienne manquée.

Amendement : le branchement runtime initial vit dans la commande `/cron`. `/cron tick` reste explicite et passe par la loop normale plutôt que d'exécuter une action directement depuis le scheduler. L'état technique minimal vit dans `~/.bb9/cron-state.json`, séparé des archives `CRON.md`.

Amendement : `Retry`, `Notification` et `History` sont des politiques déclarées dans `CRON.md`, puis interprétées par le runtime. Le scheduler calcule et applique ces politiques minimales, mais les transports de notification, l'affichage avancé d'historique et les stratégies plus fines restent des adapters branchés autour.

Amendement 2026-06-12 : `bb9 web` est un hôte explicite de routines tant qu'il tourne. Il tick les routines actives en fond, écrit les résultats dans l'accueil de l'agent ciblé et s'arrête avec le serveur web. Cela ne réintroduit pas de daemon système obligatoire.

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

Amendement : le chat web sélectionne un projet comme workspace d'exécution. Changer de projet via l'interface appelle un switch runtime contrôlé qui change le dossier courant du serveur `bb9 web`, recharge sessions, skills locaux, thèmes, Git et plan depuis ce nouveau workspace, et nettoie toute validation en attente. Le switch est refusé pendant un run actif pour éviter de changer de cwd au milieu d'une exécution.

Amendement 2026-06-09 : au démarrage, si le port demandé sert déjà un BB9 web
local d'un autre projet, le nouveau lancement demande d'abord à ce serveur de
basculer vers le dossier courant via `/api/project`, puis réutilise la même URL.
Si ce switch runtime est refusé ou indisponible, il prend le port suivant et
l'annonce explicitement.

Amendement : le projet actif choisi dans le chat web est persistant dans les
settings utilisateur. Au redémarrage, `bb9 web` reprend ce dernier projet s'il
existe encore, même si le terminal qui relance le serveur est resté dans un
autre dossier. Le cwd reste le fallback quand aucun projet web persistant valide
n'est disponible.

Amendement : `/build` ne relance plus automatiquement les tâches déjà marquées
`status: error` dans le plan. Ces erreurs sont considérées comme un état à
réviser, pas comme des tâches fraîches, afin d'éviter que leurs anciens résumés
ou blockers contaminent la prochaine action. Un retry doit être explicite avec
`/build --retry-errors`.

Amendement : le tool `shell` bloque les commandes manifestement contaminées par
du texte de bilan provider (`Status`, `Evidence`, `Blocker`, `Next suggestion`,
ou concaténation `fichier.htmlerror`). Ces actions sont invalides et doivent
être reformulées avant exécution ; ce n'est pas une question de droits.

Amendement : le chat web découvre les commandes slash natives et les commandes d'archives du projet actif via `/api/commands`, puis les expose en autocomplétion dans le composer. Il découvre aussi les thèmes web via `/api/themes`; les thèmes personnalisés sont des fichiers CSS dans `.bb9/themes/web`, `~/.bb9/themes/web` ou `bb9/chat-web/themes`.

Amendement : les thèmes web peuvent être générés depuis une couleur seed avec `bb9/chat-web/scripts/generate-theme.mjs`. Le générateur utilise `culori` et OKLCH côté build pour calculer une palette complète ; le runtime web ne charge que des variables CSS découvertes par `/api/themes`.

Amendement : le chat web peut demander l'arrêt du run courant via `/api/stop`. L'arrêt est coopératif : il est vérifié entre les décisions et les actions, sans prétendre interrompre instantanément un appel provider ou un effet de bord déjà lancé. Pendant un run, le composer reste éditable et les messages soumis sont gardés dans une queue locale modifiable jusqu'à leur envoi.

Amendement : `/compact` est disponible sur le chat web. La compaction manuelle réutilise la même logique de session courte que le CLI, et l'auto-compaction web partage les seuils communs du core : 90% pour trim, 95% pour summarization et 98% pour reset.

Amendement : une validation guardian web en attente bloque toute nouvelle exécution côté API avec `approval_pending`. La surface web met les nouvelles demandes en queue locale jusqu'à autorisation ou refus, afin de ne jamais écraser une validation courante ni produire `approval_not_found` sur un bouton encore visible.

Amendement : une validation guardian web est liée à la session et au projet actifs, et expire partout au bout de cinq minutes, pas seulement au prochain message utilisateur. Changer de session, créer une session ou changer de projet nettoie la validation en attente pour éviter les approvals fantômes.

Amendement : les commandes web longues comme `/plan`, `/build` et la continuation après approval doivent exposer un état `running` sans garder le lock global de l'API pendant l'exécution. Les endpoints de statut, historique, stop et événements doivent rester réactifs pour que la surface ne semble pas figée.

Amendement : la surface web doit éviter les pollings concurrents qui s'empilent quand le backend est lent. Les requêtes live trace et status sont bornées par un garde `in flight`, et la trace live conserve un buffer court.

Amendement : `/api/run/events` est une source live-only. Quand aucun run n'est actif, l'endpoint ne rejoue pas les événements du run précédent. La surface ignore aussi tout payload live sans `running=true` et sans `run_id`, afin qu'un nouveau tour ne démarre jamais avec une trace déjà remplie.

Amendement : la surface web ne saisit plus le modèle en texte libre par défaut. Elle consomme `/api/models`, affiche les modèles groupés par provider configuré et applique immédiatement le couple provider/modèle choisi via `/api/settings`.

Amendement : `/api/settings` est la source durable pour les préférences runtime partagées du chat web, notamment le profil de permission et le thème web. `localStorage` reste seulement un cache de surface pour éviter un flash ou survivre à une API temporairement indisponible.

Amendement : le chat web expose Git comme un panneau dédié ouvert depuis une icône, pas comme une suite de contrôles dans le header. `/api/git` fournit branche courante, branches locales et fichiers modifiés avec compteurs compacts ; `/api/git/diff` fournit le diff textuel dépliable d'un fichier ; `/api/git/branch` utilise `git switch` sans option destructive, refuse de changer de branche si le worktree est sale, et remonte les erreurs Git à l'utilisateur.

Amendement : le panneau Git du chat web peut préparer un message de commit depuis les fichiers modifiés, mais le commit reste une action explicite en deux temps. La surface affiche le message généré dans un champ éditable, puis appelle `/api/git/commit` seulement après confirmation utilisateur.

Amendement : les commandes d'archives peuvent être déclarées dans `## Commandes` ou dans le frontmatter `commands:` pour faciliter la migration de skills locaux existants. Les commandes locales du projet actif sont chargées par défaut et exposées aux surfaces via le même payload `/api/commands`.

Amendement : Ollama local et Ollama Cloud sont deux providers distincts. Ollama local utilise l'endpoint OpenAI-compatible `http://localhost:11434/v1`, sans clé API. Ollama Cloud utilise `https://ollama.com`, une clé `OLLAMA_API_KEY`, `/api/tags` pour lister les modèles et `/api/chat` pour générer. `https://ollama.com` ne doit donc pas être normalisé vers localhost.

## 2026-06-04 — Frontière de tour et livraison sketch vérifiable

Décision : l'intention courante est l'autorité du tour. La session récente reste du contexte, mais elle ne doit pas faire continuer une tâche précédente quand l'utilisateur change de sujet ou lance une commande slash.

Raison : une réponse peut sinon arriver avec un tour de retard, par exemple terminer une analyse précédente après une nouvelle commande de livraison d'artefact.

Conséquence : le prompt runtime marque explicitement la frontière de tour. Pour une commande déclarant un contrat `workspace-artifact`, la loop refuse aussi une réponse finale qui ne référence pas les fichiers produits dans le chemin déclaré par le skill, et une preview navigateur échouée doit être signalée au lieu de valider implicitement le rendu.

## 2026-06-06 — Approvals guardian mémorisés explicitement

Décision : BB9 peut mémoriser une validation guardian seulement quand l'utilisateur choisit explicitement une option de type "toujours autoriser cette action".

Raison : réduire les confirmations répétées sans reprendre le modèle trop large de shell permissif. La fluidité doit venir d'une permission exacte et traçable, pas d'une exécution moins contrôlée.

Conséquence : les approvals mémorisés vivent dans `~/.bb9/approvals.json`, avec un fingerprint basé sur le tool, les paramètres publics et le workspace. Les arguments persistés sont nettoyés des secrets apparents et les métadonnées internes de runtime sont exclues. Une approval mémorisée ne contourne pas les `block` du guardian et ne remplace pas les trusted roots.

Amendement : le chat web peut ajouter un trusted root depuis une validation `path outside workspace/trusted roots`, puis autoriser l'action courante. Ce choix écrit dans `~/.bb9/trusted-roots.md` et reste distinct d'une approval mémorisée.

## 2026-06-06 — Processus visible sans raisonnement privé brut

Décision : la loop peut émettre des événements `process` publics pour rendre le travail en cours visible dans les surfaces.

Raison : une animation générique et le nom d'un tool ne suffisent pas à donner confiance pendant les runs longs. L'utilisateur doit voir où BB9 en est : comprendre la demande, choisir une étape, vérifier les permissions, exécuter un tool, intégrer l'observation, finaliser.

Conséquence : le chat web rend un journal de travail live à partir de ces événements et des événements tools. Ce journal ne contient pas de prompt interne, pas de secrets et pas de raisonnement privé brut. L'activité visuelle est portée par les points actifs de la timeline live, pas par une animation décorative séparée.

Amendement 2026-06-09 : la couleur de la timeline web porte une sémantique stable : gris pour les étapes internes passées, jaune animé pour les actions ou subagents actifs/en attente, vert pour les états explicitement terminés, rouge pour les erreurs et blocages. Les subagents actifs doivent rester visibles même s'ils ne font plus partie des derniers événements reçus.

Amendement : un tour web ne doit afficher qu'une seule stack de trace. Pendant le run, le panel `Processus` reste ouvert et se met à jour en live ; quand la réponse finale arrive, ce même emplacement devient le message de bilan avec le panel de trace replié et réouvrable.

## 2026-06-06 — Minimalisme comme harness appropriable

Décision : le minimalisme de BB9 signifie compréhension humaine, structure lisible et appropriation par archives Markdown, pas réduction d'ambition fonctionnelle.

Raison : BB9 peut viser un assistant local complet ou un runtime lancé sur une archive spécialisée sans trahir le projet. Le risque réel n'est pas le nombre de capacités, mais le déplacement des variations durables dans du Python opaque. La référence d'esprit la plus proche est Pi Coding Agent (`https://pi.dev/`) : un harness minimal adapté par l'utilisateur à ses workflows. BB9 reprend cette logique avec un parti pris Markdown-first.

Conséquence : le coeur Python doit rester un ensemble de primitives génériques : chargement, validation, exécution, trace, permissions, providers, interfaces et runners. Les identités, comportements, workflows, politiques, prompts, routines et contrats de livraison doivent rester dans des archives Markdown quand c'est raisonnable. Une nouvelle capacité est acceptable si elle garde cette séparation et rend le système plus appropriable plutôt que plus opaque.

## 2026-06-06 — Cache des fenêtres de contexte par modèle

Décision : BB9 stocke les métadonnées de modèles, dont la fenêtre de contexte, dans `~/.bb9/model-metadata.json` et résout ces métadonnées à chaque changement explicite de modèle ou de provider actif.

Raison : l'utilisateur peut changer de modèle à tout moment. Redemander ou rechercher la fenêtre de contexte à chaque changement serait lent, répétitif et fragile. Le runtime doit connaître une limite exploitable pour borner la session, l'auto-compaction et le contexte structurel Markdown.

Conséquence : la résolution utilise le cache local, une table connue embarquée, puis un fallback prudent. Aucune requête web implicite n'est faite pendant l'auto-compaction ou le changement de modèle. Une future mise à jour web devra passer par une commande ou un tool explicite. Le contexte structurel permanent de BB9 vise une cible pratique d'environ 10% de la fenêtre connue ; si la fenêtre est inconnue, BB9 utilise le fallback et signale l'incertitude quand elle devient importante.

## 2026-06-06 — Auto-compaction visible

Décision : une auto-compaction du contexte court doit produire une notification visible et persistée dans l'historique visible.

Raison : la compaction modifie ce que BB9 garde dans son contexte actif. Même si elle ne supprime pas l'historique visible, l'utilisateur doit savoir quand la session courte vient d'être résumée.

Conséquence : les channels affichent une notification courte avec le nombre de messages compactés et conservés. Cette notification ne devient pas un message de session injecté au provider.

## 2026-06-07 — Contrats runtime centraux stabilisés

Décision : les formes minimales `Intention`, `Decision`, `Action`, `GuardianDecision`, `Observation`, `RunContext`, `RunResult` et `TraceEvent` sont les contrats runtime retenus pour le noyau actuel.

Raison : les docs `kernel`, `loop`, `guardian` et `gateway` posaient encore des questions fondamentales alors que l'implémentation a déjà stabilisé ces formes. Garder ces questions ouvertes rendait le projet plus difficile à reprendre.

Conséquence : le kernel retourne une `Decision`, la loop synchrone `run_once` garde son état de tour dans `LoopState`, les observations intermédiaires repassent au provider via les métadonnées d'intention, le guardian retourne `allow`/`ask`/`block`, et le gateway reste une façade fine vers les runtimes de tools. La dette restante porte surtout sur le protocole texte `BB9_ACTION`, la généricité des garde-fous de loop et les contrats de `review` des tools.

## 2026-06-07 — Gates qualité actuelles

Décision : les gates qualité actives sont `python3.11 -m ruff check .` et `python3.11 -m unittest discover -q`.

Raison : ces deux commandes passent et couvrent l'hygiène automatique minimale du projet. `mypy` est configuré, mais il échoue encore largement et ne doit pas être présenté comme bloquant tant que la dette de typage n'est pas traitée par lots.

Conséquence : `mypy` reste un diagnostic de stabilisation. Le rendre bloquant exigera une passe dédiée, avec correction progressive des modules et mise à jour explicite de cette décision.

## 2026-06-09 — `/build` sépare résultat, trace et réponse

Décision : `/build` produit un résultat structuré interne, des marqueurs live
diagnostiques et une réponse utilisateur distincte.

Raison : réutiliser la sortie stdout brute comme message final web rendait les
erreurs de subagents illisibles, pouvait masquer le retour réel jusqu'au
rechargement, et mélangeait progression machine, diagnostic et synthèse
canonique.

Conséquence : le chat web affiche une synthèse courte de `/build` et conserve
la sortie brute comme artefact diagnostique caché par défaut. Les traces de
subagents peuvent être attachées à cet artefact pour expliquer les erreurs sans
polluer la conversation.

Amendement : dans un retour de subagent, un `Status: done` explicite prévaut
sur une section `Blockers` utilisée comme réserve ou limite. Les skips
`dependency:*` dans `.bb9/plan.md` sont des blocages recalculables, pas des
erreurs directes persistantes ; l'UI web les affiche séparément des erreurs
rouges.

## 2026-06-09 — Les approvals web sont reprenables

Décision : un `ask` guardian dans le chat web est une pause reprenable, y
compris quand il survient dans un subagent lancé par `/build`.

Raison : un agent ne doit pas rester dans l'impasse pour une question de droits.
Quand une action est acceptable sous confirmation, l'utilisateur doit pouvoir
l'autoriser et débloquer le même travail, ou la refuser et laisser l'agent
chercher une alternative.

Conséquence : les validations web conservent le contexte nécessaire à la
continuation. Dans `/build`, la tâche en cours n'est pas écrite en erreur
pendant l'attente. Après `allow`, le subagent reprend avec l'observation de
l'action exécutée. Après `deny`, il reprend avec une observation de refus et
doit soit utiliser une autre action autorisée, soit expliquer le blocage. Le
build web sérialise les tâches en profil `safe`, où les approvals interactives
sont fréquentes, afin d'éviter des validations concurrentes impossibles à
reprendre proprement. En `limited` et `power`, les tâches parallélisables
restent exécutées en parallèle et exposées comme branches de subagents dans la
trace.

Amendement : quand un subagent déclenche plusieurs `ask` guardian dans une même
tâche, chaque demande remonte au channel parent avec le task id, le titre et le
worker concernés. Le subagent ne parle pas directement à l'utilisateur et ne
peut pas auto-valider l'action ; l'utilisateur garde la décision `allow` ou
`deny` à chaque étape de reprise.

## 2026-06-09 — Les blocks guardian sont catégorisés

Décision : les verdicts guardian `block` portent une catégorie diagnostique
dans la trace publique et les observations de loop.

Raison : tous les blocks ne signifient pas la même chose. Un chemin protégé est
un refus sécurité, une action mal formée doit être reformulée, et une syntaxe
shell non supportée doit être remplacée par une action plus simple. Les mêler
rend l'UI anxiogène et laisse croire à un problème de droits.

Conséquence : la loop expose `block_category` avec les valeurs
`security`, `invalid_action`, `unsupported_syntax` ou `policy`. Cette catégorie
n'autorise rien ; elle aide l'agent et la surface à expliquer pourquoi l'action
n'a pas été exécutée et quel type de reprise est attendu. Après des blocks
répétés, la loop peut produire une réponse finale déterministe avec la catégorie
et la raison du dernier block si la réponse modèle est trop vague.

## 2026-06-11 — Les subagents vivent dans le pool plat des agents

Décision : un subagent est un agent du pool `~/.bb9/agents/` marqué
`Type : subagent` dans son `IDENTITY.md`. Il se gère comme un agent (mêmes
fichiers, même éditeur). Un agent normal peut faire spawn tout subagent du
pool, sauf ceux listés dans son `SUBAGENTS_DISABLED.md` — même convention
défaut-actif que les skills et les tools. Un subagent ne liste pas de
subagents : il ne spawne pas sans règle explicite.

Raison : la forme nichée `<agent>/subagents/<nom>/` dupliquait les
spécialisations entre parents et imposait une gestion différente de celle des
agents. Le pool plat garde une seule manière de décrire une identité
exécutable et rend le partage explicite.

Conséquence : `load_subagent` résout d'abord la forme nichée existante
(spécialisation possédée par le parent, prioritaire sur collision de nom),
puis le pool plat filtré par `SUBAGENTS_DISABLED.md`. L'héritage parent reste
identique : `IDENTITY.md`, `SOUL.md`, `MODEL.md` retombent sur le parent ;
les listes disabled s'ajoutent à celles du parent. L'index injecté au parent
liste l'union des deux formes.

## 2026-06-12 — `/goal` est une commande d'orchestration, pas un agent

Décision : supprimer `goal` comme agent ou subagent conventionnel. `/goal`
reste une commande native qui porte un objectif persistant, lance des
iterations, verifie les preuves et laisse l'evaluateur runtime decider du
succes, du blocage, de la pause ou de la limite.

Raison : un goal n'est pas une identite. Le modeliser comme subagent melangeait
deux surfaces : les agents de travail configurables et le mode long qui permet
a l'agent courant d'enchainer sur une intention.

Conséquence : les templates `agents/goal` et `default/subagents/goal` sont
retirés. Les iterations `/goal` utilisent le worker `dev` s'il existe, sinon
un worker `dev` ephemere issu du template generique. Les optimisations de
modele pour les goals devront passer par la configuration du worker `dev` ou
par une future configuration propre aux goals, pas par un agent `goal`.

## 2026-06-12 — Guardian : erreurs de formulation ≠ permission, et `power` autorise par défaut dans le périmètre

Décision : séparer les trois verdicts que le guardian confondait. Une action
mal formée (`invalid_action`) ou une syntaxe non exécutable
(`unsupported_syntax`) n'est plus un blocage guardian : la loop la transforme
en observation corrective avec l'usage attendu du tool (`usage()` exposé par le
runtime), et le modèle a plusieurs tentatives pour reformuler avant d'être
forcé de répondre. Seuls `security` et `policy` restent des blocages réels. En
profil `power`, les commandes inconnues et destructives passent sans validation
tant que les chemins restent dans le workspace ou un trusted root ; `ask` est
réservé aux sorties de périmètre et à la liste à confirmation systématique
(`sudo`, `dd`, `mkfs`, `mount`, `umount`, `chown`). Le préfixe
`cd <dossier> && <commande>` est normalisé : zone du dossier vérifiée, `cd`
retiré, commande exécutée avec ce répertoire de travail.

Raison : le guardian produisait des asks parasites (commande inconnue en power,
`cd` non reconnu) et des fins de tour en erreur sur de simples fautes de
syntaxe d'action, y compris pour des actions qui auraient échoué même
approuvées (`cd` n'est pas un binaire). Claude Code et Codex traitent l'input
malformé comme une erreur de tool que le modèle corrige silencieusement, et
réservent la validation aux vraies frontières.

Conséquence : un guardian discret dans le périmètre, ferme aux frontières.
Les heuristiques anti-contamination exigent désormais deux marqueurs (plus de
faux positif sur `grep error: app.log`) et `...` n'est un placeholder que
comme token isolé (les ranges git `main...develop` passent). Restent ouverts :
mémorisation d'approbation généralisée (préfixe de commande plutôt
qu'empreinte exacte) et branchement de Telegram sur l'ApprovalStore.

## 2026-06-13 — Pas de modale tools : les tools sont un équipement de base, les extensions sont des skills

Décision : supprimer l'item « Capacités » du menu web (placeholder d'une modale
tools jamais branchée) et ne pas créer de gestion CRUD des tools. Les tools
natifs sont l'équipement de base livré avec BB9 : ni créables ni supprimables
depuis une conversation ou l'interface. Leur seule gestion utilisateur est
l'activation par agent dans la gestion des agents, complétée d'un paramétrage
par tool : un tool déclare ses paramètres dans la section `Secrets requis` de
son `TOOL.md` (références `secret:NOM`) et la gestion des agents affiche un
formulaire sous chaque tool activé qui en déclare ; les valeurs vont dans le
store de secrets local, seul l'état défini/non défini est réaffiché. Toute
extension utilisateur est un skill — y compris une vraie capacité avec
`runtime.py`/`core/` — et vit dans un dossier skills ; `create_skill` n'écrit
que là (noms normalisés, racines fixes) et le template `extension-factory`
perd `/create-tool` : créer un tool natif est une contribution au dépôt, pas
une extension. La modale skills est renommée « Capacités & Skills ».

Raison : un créateur/suppresseur de tools dupliquerait le chemin skills en
moins sûr, et les tools de base sont tous utiles ; le besoin réel était le
paramétrage (caldav) et la lisibilité (blocs skills/tools en pleine largeur
dans la gestion des agents).

Conséquence : la frontière extension/natif est contractuelle de bout en bout :
TOOL.md déclare, l'API n'accepte que les paramètres déclarés, l'UI n'expose ni
création ni suppression de tools, et `extension-factory` redirige toute
demande de capacité utilisateur vers un skill.

## 2026-06-13 — Modale Agents en onglets, menu allégé, gestion durable des projets

Décision : restructurer la navigation web. La modale Agents devient trois
onglets (Paramètres / Skills / Tools) car elle mélangeait identité, modèle,
Telegram, skills, tools et subagents sur une seule page trop chargée. Le menu
latéral est nettoyé : l'item « Paramètres » (placeholder sans modale) est
retiré, « Capacités & Skills » redevient « Skills », et un item « Projets »
apparaît. La modale Projets pilote une liste blanche durable de chemins
(`settings.json` → `projects`), fusionnée avec les projets détectés via les
sessions : ajouter, retirer, éditer (suivi de déplacement) et activer un
projet — activer l'un désactive les autres (un seul projet actif = workspace
d'exécution).

Raison : la modale Agents grossissait à chaque capacité ajoutée (Telegram,
paramètres de tools) ; le menu portait un item mort et un libellé ambigu ; et
la gestion des projets n'existait que par détection implicite, sans moyen
d'enregistrer durablement, de nettoyer ou de corriger un chemin déplacé.

Conséquence : `SettingsStore` porte un registre `projects` (tous les setters
passent par `replace` pour ne plus s'écraser entre eux),
`known_project_candidates` lit ce registre en plus des sessions, et l'API
expose `update_projects` (add/delete/edit) à côté du `switch_project`
existant. La règle « un seul projet actif » reste portée par
`web_project_path` ; la liste blanche n'élargit aucun droit et n'accepte qu'un
dossier existant.

## 2026-06-13 — Notes & todos par agent : tool natif + espace dans le dossier de l'agent

Décision : doter chaque agent d'un espace de notes Markdown et d'une todo list,
stockés dans son propre dossier (`agents_dir/<agent>/notes/<slug>.md` et
`agents_dir/<agent>/TODO.md`). La logique pure vit dans `bb9/core/notes.py`,
réutilisée par un tool natif `notes` (que l'agent utilise sans accès au
workspace, le dossier agent étant résolu depuis le contexte) et par l'API web.
Un bloc compact des tâches ouvertes et des titres de notes est injecté dans le
contexte de chaque tour (`RunContext.notes_context`), pour que l'agent sache que
ces notes existent. L'interface ajoute un item « Notes & todos » ouvrant une
modale en deux sections (todo en haut, fichiers notes en bas) avec CRUD complet.

Raison : un agent a besoin d'une mémoire de travail durable et d'un suivi de
tâches, distincts du workspace projet. Passer par le dossier de l'agent garde
ces données rattachées à l'identité de l'agent et hors du dépôt projet ; un tool
natif évite à l'agent de demander une écriture hors workspace (qui serait `ask`
à chaque fois via `files`/`shell`).

Conséquence : nouveau champ `RunContext.notes_context` rendu par le kernel ;
tool `notes` livré avec BB9 (lecture `allow`, écriture `allow` en limited/power,
`ask` en safe) ; endpoints `/api/notes`, `/api/notes/update`,
`/api/todos/update`. Les notes ciblent l'agent canonique, pas un subagent
éphémère, pour que web et runtime partagent la même vue. Documenté dans
`docs/notes.md`.

## 2026-06-13 — Index runtime compact pour skills/tools actifs

Décision : les index `Skills Index` et `Tools Index` injectés au provider sont
des index runtime compacts, pas des catalogues exhaustifs d'interface. Ils ne
contiennent que les skills/tools actifs pour l'agent courant et gardent une
forme courte : nom, résumé borné, commandes slash utiles et protocole d'action
minimal quand il existe. L'interface web continue de reconstruire son inventaire
exhaustif depuis les archives Markdown et les fichiers `*_DISABLED.md`, sans
dépendre du contenu de ces index. Le CLI expose aussi `/skills` et `/tools`
pour lister, activer ou désactiver les archives de l'agent actif en écrivant les
mêmes fichiers Markdown que le web.

Raison : les petits modèles locaux décrochent vite quand le prompt porte un
catalogue explicatif de toutes les capacités. Le modèle a besoin de savoir ce
qui est actif et comment appeler une capacité plausible ; l'humain et l'UI ont
besoin d'un inventaire complet inspectable. Mélanger ces deux vues gonfle le
contexte sans ajouter de contrôle.

Conséquence : la source de vérité reste `TOOL.md` / `SKILL.md` plus les listes
`SKILLS_DISABLED.md` et `TOOLS_DISABLED.md`. Le runtime en dérive une projection
compacte pour le modèle ; les surfaces de gestion en dérivent une projection
exhaustive pour l'utilisateur. Les surfaces qui ne portent pas d'UI riche
peuvent au minimum consommer et modifier les listes disabled sans recopier la
logique du web.

## 2026-06-13 — Budget de worker borné par `max_iterations`

Décision : quand `/build` délègue une tâche, `Task.max_iterations` devient le
budget d'actions outil du worker, au lieu de laisser le subagent hériter du
budget global du profil (`power`, `limited` ou `safe`).

Raison : sur un petit modèle local, une tâche simple peut boucler longtemps sur
des actions, corrections ou formulations avant de rendre la main. Le plan porte
déjà le champ `max_iterations`; ne pas l'appliquer rendait les tâches
faussement bornées et pouvait produire des timeouts provider.

Conséquence : `/plan` doit renseigner `max_iterations` explicitement. Les plans
anciens ou incomplets gardent un défaut de `4` actions pour éviter de casser les
tâches qui demandent plusieurs validations utilisateur. Une tâche simple peut
être abaissée à `1`; une tâche qui doit lire puis modifier ou vérifier doit
demander `2` à `4`; davantage doit être justifié par une tâche réellement
étendue.
