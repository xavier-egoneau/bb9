# BB9 Rules

Ce fichier synthétise les règles de fonctionnement de BB9.

Il ne remplace pas les contrats détaillés dans `docs/`, `DECISIONS.md` et les
archives Markdown. Il sert de carte courte pour vérifier qu'une évolution reste
alignée avec l'architecture BB9.

## Règles Transversales

- BB9 ne fait pas moins fonctionnellement ; il place mieux la complexité.
- Le Markdown porte l'intention, la configuration, les comportements, les politiques et les workflows.
- Python charge, valide, exécute, indexe, adapte et sécurise.
- Le kernel reste petit et ne possède pas les briques métier.
- Les interfaces restent remplaçables.
- Les surfaces peuvent changer de rendu, mais elles doivent préserver le même service.
- Un dashboard ne doit jamais devenir le coeur du système.
- Les secrets bruts ne doivent jamais être écrits dans les Markdown, logs, traces, prompts ou index.
- Tout effet de bord doit passer par la loop, les hooks, le guardian et le gateway.
- Les tools répondent à l'agent ; l'utilisateur reçoit un bilan naturel rédigé par l'agent.
- Un tool ne doit pas court-circuiter l'agent dans la réponse finale à l'utilisateur.
- Une analyse de repo doit produire une synthèse utile, pas un inventaire de fichiers.
- Les fichiers, APIs et méthodes ne sont cités que s'ils appuient une conclusion ou une recommandation.
- Un listing d'arborescence est réservé aux demandes explicites de structure ou d'inventaire.
- Les états runtime vivent dans le dossier user ou le workspace, pas dans les contrats Markdown source.
- Les fichiers générés doivent être régénérables ou explicitement persistants.

## Archives Markdown

- Une brique durable doit être représentable comme une archive Markdown lisible.
- Une archive est un dossier nommé avec un fichier principal explicite.
- Le fichier principal doit permettre de comprendre la brique sans lire son code.
- Les noms d'archives doivent rester stables et simples.
- Les fichiers Python optionnels ne remplacent jamais le contrat Markdown.
- Le frontmatter peut porter des métadonnées courtes, pas un langage caché.
- Les désactivations doivent rester en Markdown.
- Les index générés résument les archives ; ils ne doivent pas injecter tout le contenu partout.
- Les commandes d'une archive vivent dans sa section `## Commandes`.
- Une commande native du REPL gagne toujours sur une commande d'archive.
- Deux archives actives qui déclarent la même commande créent un conflit visible.
- Une commande d'archive en conflit ne doit pas être routée automatiquement.
- Une archive qui demande trop de Python spécifique doit être redécoupée.

## Kernel

- Le kernel transforme une intention et un contexte en décision exploitable.
- Le kernel peut appeler un provider abstrait, jamais un provider concret en dur.
- Le kernel peut recevoir la memory et le context-index comme contexte préparé.
- Le kernel ne persiste rien lui-même.
- Le kernel ne lit pas le disque pour découvrir agents, skills, tools ou dreams.
- Le kernel ne gère pas les permissions.
- Le kernel ne contient ni logique UI, ni logique réseau, ni logique métier de tool.
- Le provider peut demander une action seulement via un protocole structuré.
- Les demandes `BB9_ACTION` passent ensuite par la loop, les hooks, le guardian et le gateway.
- Pour une analyse de projet, le kernel doit orienter le provider vers verdict, risques et priorités plutôt que vers un listing.

## Context Runtime

- `context_runtime.py` assemble agent, session, workspace, skills, tools, trusted roots, subagents index et context-index.
- Un channel demande un `RunContext` ; il ne reconstruit pas lui-même la découverte runtime.
- Le context runtime ne décide pas de l'intention utilisateur.
- Le context runtime ne choisit pas le provider.
- Le context runtime ne contourne pas le guardian.
- Le context runtime peut régénérer des index locaux explicitement régénérables.
- Le context runtime ne doit pas devenir une mémoire durable ni un workflow engine.

## Loop

- La loop orchestre le cycle agentique.
- La loop garde le chemin d'exécution lisible.
- La loop limite le nombre d'itérations et le budget de tools.
- La loop distingue intention, décision, action, observation et trace.
- Toute action passe par `pre-action hook -> guardian -> gateway -> tool -> post-action hook`.
- Si le guardian bloque ou demande validation, la loop ne cherche pas de contournement.
- La loop ne devient pas un moteur de workflow généraliste.
- Les goals ajoutent une orchestration au-dessus de `run_once`, pas une loop parallèle.

## Guardian

- Le guardian décide si une action peut atteindre le gateway.
- Le guardian combine zone, risque, profil de permission et règles absolues.
- Les profils sont `safe`, `limited` et `power`.
- Les profils augmentent l'autonomie dans le périmètre autorisé ; ils ne suppriment pas les règles absolues.
- `workspace` et `trusted root` sont des zones de travail.
- `outside` demande validation avant ajout aux trusted roots.
- `protected` est bloqué.
- Les créations et modifications simples dans le workspace ou un trusted root sont des écritures normales, pas des validations obligatoires.
- Les suppressions, secrets, permissions, commandes destructives, réseau et sorties de périmètre restent sensibles.
- Une suppression dans le workspace peut être soumise au guardian et demander validation ; elle ne doit pas être refusée par principe par le modèle.
- Le guardian ne stocke ni n'affiche de secrets.
- Aucun provider, kernel, subagent, cron ou channel ne doit contourner le guardian.

## Gateway

- Le gateway exécute seulement des actions structurées et autorisées.
- Le gateway vérifie qu'une autorisation explicite existe.
- Le gateway retourne une observation claire.
- Le gateway isole les accès fichiers, shell, réseau et providers.
- Le gateway respecte le workspace courant par défaut.
- Le gateway ne décide pas de l'objectif utilisateur.
- Le gateway ne cache pas les échecs.
- Le gateway ne devient pas propriétaire de la session complète.
- Un mode continu futur doit rester explicite, interrompable et soumis au guardian.

## Hooks

- Les hooks vivent sur le chemin obligatoire entre décision et tool.
- Le pre-action hook prépare l'examen du guardian.
- Le post-action hook sécurise l'observation après exécution.
- Les hooks peuvent masquer les secrets et produire des événements de trace.
- Les hooks ne doivent pas exécuter les effets de bord.
- Les hooks ne doivent pas devenir un workflow engine caché.
- Le post-action hook ne peut pas autoriser rétroactivement une action interdite.

## Channels Et CLI

- Un channel reçoit une entrée et restitue une réponse.
- Un channel ne contient pas la logique décisionnelle.
- Les channels doivent exposer les mêmes services autant que leur transport le permet.
- Une différence de surface doit être une adaptation de rendu, pas une divergence métier.
- Quand un canal ne supporte pas une feature complète, il fournit une dégradation explicite.
- Les traces, artefacts, confirmations, commandes et notifications doivent avoir un équivalent par surface quand c'est possible.
- Les primitives de rendu communes sont `activity_indicator`, `live_tool_use`, `tool_trace`, `code_block`, `visible_process`, `todo_list`, `diff`, `artifact_list`, `approval`, `error_detail` et `notification`.
- Si l'agent est actif, la surface doit le montrer par une animation, un statut ou un message de progression.
- Dans le CLI, l'activité LLM est un point de focus animé, éphémère, qui ne pollue ni le contexte ni l'historique.
- Un tool en cours doit avoir un marqueur live distinct de la trace de tool terminé.
- Un tool terminé doit laisser une trace visible avec nom, statut et résumé court.
- Une commande `shell` visible doit être rendue comme bloc `bash` quand la surface le permet.
- La sortie brute d'un tool reste destinée à l'agent ; elle ne remplace pas le bilan naturel.
- Une trace live ne doit pas afficher une page HTML ou une observation longue brute comme résumé.
- Le processus visible est un résumé de progression ; il ne révèle pas le raisonnement privé brut.
- Une trace visible de tool liste l'outil, le statut et un résumé humain, pas l'observation brute complète.
- Un diff visible est attaché au tour qui a modifié les fichiers.
- Un diff visible est plié par défaut et se déplie fichier par fichier.
- Le premier niveau d'un diff affiche le nombre de fichiers modifiés, les totaux `+/-` et la liste des fichiers touchés.
- Dans le CLI, le diff immédiat reste un résumé compact ; le patch complet reste un artefact.
- Une surface riche peut afficher une carte de revue ; une surface simple dégrade vers Markdown, fichier `.diff` ou lien d'artefact.
- Les commandes REPL sont une syntaxe locale, pas la définition du service.
- Le CLI peut rendre un Markdown léger en ANSI, mais doit garder le Markdown brut en sortie non interactive.
- La coloration syntaxique CLI reste légère et opportuniste ; elle ne doit pas exiger une dépendance lourde.
- Le rendu Markdown CLI améliore la lisibilité ; il ne devient pas une surface propriétaire.
- Les messages utilisateur doivent être visuellement distincts dans le CLI sans être recopiés ni modifier le contenu persisté.
- Le chat web, Telegram, le CLI ou un dashboard ne deviennent jamais propriétaires de la source de vérité.
- Le REPL est le premier channel local.
- Le REPL peut enregistrer des commandes slash, intercepteurs, handlers guardian et lignes de contexte.
- Le REPL charge les extensions via les chargeurs génériques.
- Le REPL transmet une commande slash inconnue au kernel quand son nom correspond à un skill actif.
- Le REPL ne doit pas importer un à un les fichiers métier des tools ou skills.
- Une capture locale temporaire peut recevoir une valeur sensible sans l'envoyer au provider.
- `cli.py` est le host REPL et une façade de compatibilité, pas le propriétaire de toutes les commandes.
- Les flux interactifs spécialisés vivent dans des modules `*_cli.py`.
- Un module `*_cli.py` peut parser une commande, afficher un résultat et appeler les contrats runtime.
- Un module `*_cli.py` ne doit pas cacher de logique métier durable qui appartient à une archive ou à un module runtime.
- Les façades stables peuvent rester dans `cli.py` quand elles protègent les tests, les extensions ou les appels existants.

## Modules CLI Spécialisés

- `dream_cli.py` porte l'expérience `/dream`, pas le moteur de dreaming.
- `cron_cli.py` porte l'expérience `/cron`, pas le calcul pur des échéances.
- `goal_cli.py` porte l'expérience `/goal`, pas l'orchestration persistante.
- `session_cli.py` porte les commandes de session et compaction, pas la base de sessions.
- `provider_cli.py` porte le wizard `/model`, pas la construction runtime du provider.
- `extensions_cli.py` porte le chargement des extensions REPL, pas les archives skills/tools.
- Les modules CLI spécialisés appellent `dream.py`, `cron.py`, `sessions.py`, `memory.py`, `tools.py` et `skills.py` comme des contrats.
- `provider_runtime.py` construit les providers utilisables par le REPL, goals, cron, dream et agents.
- `context_runtime.py` assemble le `RunContext` utilisable par le REPL, goals, cron, dream et futurs channels.
- `cli.py` garde seulement des façades courtes vers `provider_runtime.py` quand c'est utile à la compatibilité.
- Les modules CLI spécialisés doivent rester petits, testables par leur sortie et remplaçables par un futur channel.
- Ajouter une commande slash importante doit d'abord chercher son module `*_cli.py` naturel.

## Agents

- Les agents actifs vivent dans `~/.bb9/agents/<name>/`.
- `IDENTITY.md` et `SOUL.md` sont du contexte actif, pas de la décoration.
- `MODEL.md` peut surcharger le modèle et le `ReasoningEffort`, pas la config sensible du provider.
- Un agent reçoit les skills et tools disponibles sauf désactivation explicite.
- `SKILLS_DISABLED.md` et `TOOLS_DISABLED.md` sont des listes Markdown.
- Un agent ne possède pas la mémoire durable.
- Un agent ne contourne pas le guardian.
- Le repo ne garde que des templates d'agents.

## Subagents

- Un subagent est une délégation bornée, pas un second système.
- Un subagent vit sous `~/.bb9/agents/<agent>/subagents/<subagent>/`.
- Un subagent hérite du parent pour les fichiers absents.
- Les désactivations de skills/tools du subagent s'ajoutent à celles du parent.
- Un subagent reçoit une tâche standalone, un contexte suffisant et des droits explicites.
- Le parent doit mâcher la tâche comme une user story autonome avant délégation.
- Un subagent retourne un `TaskResult` exploitable par la loop principale.
- Le parent garde la trace canonique visible par l'utilisateur.
- Un subagent ne possède pas la mémoire durable.
- Un subagent ne lance pas d'autres subagents sans règle explicite.
- La délégation runtime construit un contexte réduit et une session `delegation:<task-id>`.
- Le profil de permission d'une tâche ne peut pas dépasser celui du parent.
- `subagents/default` est un fallback de délégation bornée, pas l'agent parent bis.

## Plan Et Dev

- `/plan` est un skill de découpage, pas un runner.
- `/plan` produit des tâches, dépendances, critères de done et possibilités de parallélisation.
- `/dev` est un skill d'exécution de plan, pas une loop parallèle libre.
- `/dev` attend les dépendances avant de lancer une tâche.
- `/dev` peut lancer sans attendre une tâche explicitement parallélisable.
- `/dev delegate` est le premier branchement explicite vers le runtime de délégation.
- `/dev delegate` exige une tâche standalone sous forme de champs explicites.
- `/plan` écrit le plan courant dans `.bb9/plan.md` et écrase l'ancien plan.
- `/dev` lit `.bb9/plan.md` et exécute le plan séquentiellement.
- `/dev` coche les tâches réussies dans `.bb9/plan.md`.
- `/dev` écrit sous chaque tâche exécutée un état court `status`, `summary`, et si besoin `blockers` ou `evidence`.
- `/dev` lance en parallèle seulement les tâches prêtes, `parallelizable: true`, avec `paths:` non vide et sans conflit de paths.
- Une tâche sans `paths:` reste séquentielle.
- Une tâche parallélisable ne doit pas modifier la même zone qu'une autre tâche en cours sans règle claire.
- Une task déléguée doit contenir objectif, contexte, contraintes, résultat attendu et critères de done.
- Un `TaskResult` a un status `done` ou `error` et fournit résumé, preuves et bloqueurs.
- Les ids de tâches restent internes au Markdown ; la trace et le récap `/dev` utilisent les titres humains des tâches.
- Le runtime futur de délégation doit rester `delegate(task, subagent) -> TaskResult`.
- Le premier runtime garde un runner injecté pour éviter de coupler délégation, CLI et provider.
- Aucun skill `/plan` ou `/dev` ne donne de permission implicite hors guardian/gateway.
- Les templates `/plan` et `/dev` vivent dans les skills utilisateur et peuvent être adaptés sans toucher au kernel.

## Skills

- Les skills utilisateur vivent dans `~/.bb9/skills/<name>/SKILL.md`.
- Les skills locaux vivent dans `.bb9/skills/<name>/SKILL.md` du workspace courant.
- Un skill local prend le dessus sur un skill global du même nom.
- Un skill est une archive utilisateur autonome et partageable.
- Un skill peut agir, définir une méthode, une posture, une commande ou un comportement attendu.
- Les commandes d'un skill vivent dans son archive Markdown et son `cli.py` optionnel.
- Une commande de skill déclarée dans `## Commandes` peut servir d'alias Markdown pur.
- Les nouveaux skills doivent préférer `/<skill>` et `/<skill>-<commande>` aux alias courts.
- Une commande slash inconnue qui correspond au nom d'un skill actif est traitée comme une intention skill.
- Un skill doit rester portable et éviter les chemins locaux en dur.
- Un skill ne contient pas de secret.
- Un skill ne remplace pas le kernel.
- `runtime.py`, `cli.py` et `core.py` de skill sont du code local de confiance ; ils doivent être relus avant activation.
- Un skill peut orienter l'agent vers d'autres skills ou tools existants.
- Toute action concrète d'un skill passe par guardian puis gateway.
- Un skill peut fournir un `DREAM.md` comme contribution au dreaming.

## Tools

- Les tools natifs vivent dans `bb9/tools/<name>/TOOL.md`.
- Un tool est une archive native autonome livrée avec BB9.
- Un tool peut agir, définir une méthode, une commande ou un comportement attendu.
- Un tool déclare son usage, son protocole, ses permissions, ses effets et ses limites.
- Les commandes d'un tool vivent dans son archive Markdown et son `cli.py` optionnel.
- Un tool exécutable garde son backend près de son archive.
- Le core fournit le chargeur générique ; il n'accumule pas les implémentations métier.
- Le modèle ne peut jamais appeler un tool directement.
- Toute action concrète de tool passe par guardian puis gateway.
- Un tool ne cache pas ses effets de bord.
- Une observation de tool est une donnée technique pour l'agent, pas une réponse utilisateur brute.
- Un tool ne reçoit une commande REPL que pour une vraie surface humaine ou système, pas pour exposer un raccourci métier.
- Un tool ne devient pas un mini-agent autonome.
- Un tool peut fournir un `DREAM.md` comme contribution au dreaming.

## Tool Runtime

- `runtime.py` est la porte d'entrée action d'une archive skill/tool.
- `cli.py` est la porte d'entrée REPL d'une archive skill/tool.
- `core.py` est un backend optionnel importé par `runtime.py` ou `cli.py`.
- `core/core.py` est accepté quand le backend a besoin d'un petit dossier.
- `runtime.py` peut exposer `action_from_text`, `review` et `execute`.
- `cli.py` peut exposer `register(cli)`.
- `runtime.py` est le chemin normal pour soumettre une capacité à l'agent.
- `cli.py` est réservé aux surfaces humaines explicites, captures locales et interactions système.
- Les modules de runtime sont chargés dynamiquement par nom validé.
- Un runtime d'archive retourne une `Observation`.
- Une `Observation` est faite pour la loop et l'agent ; elle n'est pas l'UX finale.
- Une review spécifique d'archive ne remplace pas le guardian global.
- Les entrées invalides doivent être bloquées ou observées clairement.
- Un tool indisponible structurellement pendant un tour ne doit pas être relancé en boucle.
- Un tool dont l'index indique `unavailable` ne doit pas être appelé pour tester sa disponibilité.
- Un agent qui promet de modifier un fichier doit utiliser un tool d'édition dans le même tour ou expliquer le blocage concret.
- `files` est le tool natif pour les écritures fichier bornées (`write`, `replace`, `insert_before`, `insert_after`).

## Workspace Et Trusted Roots

- Le workspace est le périmètre local courant du run.
- Les actions fichier et shell sont limitées au workspace et aux trusted roots.
- Les trusted roots vivent dans `~/.bb9/trusted-roots.md`.
- Un workspace ne peut pas s'accorder lui-même une permission globale.
- Les actions hors périmètre demandent validation.
- Les chemins protégés restent bloqués.
- En `limited` et `power`, les modifications fichier bornées dans le workspace ou un trusted root ne doivent pas demander d'ask.
- En `limited` et `power`, les lectures shell connues dans le workspace ou un trusted root ne doivent pas produire d'ask répétitifs.
- Les pipelines de lecture simples peuvent être normalisés en commande directe sans `shell=True`.
- `grep` sans correspondance est une observation vide, pas une erreur de tool.
- `python3 -m http.server <port>` dans le workspace est un serveur local de prévisualisation ; en `limited` et `power`, il peut démarrer sans ask et sans bloquer la loop.
- Un serveur local lancé par le shell doit être borné à `127.0.0.1` sauf validation explicite.
- Un serveur local lancé en arrière-plan ne doit pas garder des pipes stdout/stderr fermables qui cassent les réponses HTTP.
- Un serveur local ne doit retourner `ok` qu'après validation d'une réponse HTTP réelle.
- Les changements doivent rester inspectables.
- Le système doit fonctionner sans imposer Git.

## Config Et Settings

- La config doit rester locale, lisible et non sensible.
- Les providers configurés vivent dans `~/.bb9/providers.json`.
- Le profil de permission courant vit dans `~/.bb9/settings.json`.
- `--profile` surcharge le lancement courant sans forcément modifier le réglage persistant.
- Les agents et subagents peuvent surcharger le modèle, pas dupliquer les secrets.
- La config ne doit pas devenir un langage de programmation.
- Les valeurs sensibles restent en variables d'environnement, fichiers locaux ou store secret.

## Installation Et Packaging

- L'installateur utilisateur unique vit dans `bb9/install.py`.
- L'installation utilisateur se lance avec `python3.11 -m bb9.install` ou `py -3.11 -m bb9.install`.
- Il ne doit pas y avoir de wrapper `install.py` racine si le module d'installation suffit.
- L'installateur exige Python 3.11+ avant d'écrire quoi que ce soit.
- Le lanceur `bb9` doit réutiliser l'exécutable Python ayant lancé l'installation.
- Le dossier de commande utilisateur doit être ajouté au `PATH` quand c'est possible.
- L'ajout au `PATH` doit être idempotent et remplacer les anciens blocs BB9.
- Le packaging standard doit rester valide pour `pip install -e .`, venv et pipx.
- `pyproject.toml` doit exposer la console script `bb9`.
- Les fichiers Markdown, tools et templates nécessaires au runtime doivent être inclus dans le package.
- Les caches, `.DS_Store`, fichiers générés et états runtime ne doivent pas entrer dans le package.

## Providers

- Un provider expose une interface minimale commune.
- Le kernel dépend d'une abstraction, pas d'un fournisseur précis.
- Les adapters provider vivent hors du kernel.
- Le provider ne peut jamais appeler un tool directement.
- Le provider ne doit jamais recevoir les secrets bruts s'ils ne sont pas nécessaires au transport.
- Les erreurs provider doivent être exploitables.
- BB9 doit rester utile sans réseau quand une étape locale suffit.
- Le provider OpenAI-compatible reste le chemin simple de base.
- Ollama local est un provider OpenAI-compatible dédié, sans clé API, sur `http://localhost:11434/v1`.
- Ollama Cloud est un provider natif dédié, avec `OLLAMA_API_KEY`, `/api/tags` pour les modèles et `/api/chat` pour générer.
- Le provider ChatGPT web reste expérimental.
- La construction runtime des providers vit dans `provider_runtime.py`.
- L'expérience interactive de configuration vit dans `provider_cli.py`.
- Le wizard `/model` doit tenter de lister les modèles disponibles après configuration du secret.
- Une clé brute collée dans `/model` doit être capturée en secret local, pas stockée comme `env:<valeur>` ni réaffichée.

## Secrets

- Les secrets bruts ne vivent pas dans le repo.
- Les secrets sont référencés par `secret:NOM`, `env:NOM` ou `file:/path`.
- Les secrets nommés vivent dans `~/.bb9/secrets/named/`.
- L'écriture d'un secret est toujours une action `ask`.
- La capture d'un secret est locale et n'est pas envoyée au provider.
- Le REPL peut intercepter une entrée qui ressemble à un secret avant appel provider.
- Les observations, logs, traces, sessions et index doivent masquer les secrets.
- Les tools dépendants de secrets déclarent les noms attendus et renvoient vers le tool `secret`.

## Session

- La session porte le contexte court actif.
- La session complète la memory durable sans la remplacer.
- La session garde les messages récents, l'état de tâche et les observations utiles.
- La session peut être compactée, archivée ou oubliée.
- La session ne stocke pas de secrets bruts.
- La session ne devient pas une mémoire long terme.
- La session persistée vit dans `~/.bb9/sessions.db`.
- La persistance de session est un état runtime, pas un Markdown édité par le système.
- Le dreaming peut lire les sessions, mais ne promeut que des faits durables et sourcés.
- L'historique visible vit dans `~/.bb9/visible-history.db`, séparé de la session courte.
- L'historique visible garde les messages relisibles et les artefacts, pas le raisonnement privé.
- `/history` exporte l'historique visible en Markdown portable.

## Compaction

- La compaction résume les anciens messages et conserve les récents.
- La compaction reste interne à la session.
- La compaction ne modifie pas `MEMORY.md`.
- L'auto-compaction utilise les métadonnées locales du modèle et des seuils prudents.
- L'auto-compaction ne fait pas de requête web implicite.
- La compaction actuelle est déterministe et locale.

## Memory

- La memory garde les faits durables, utiles et validés.
- La memory vit dans `~/.bb9/memory.db`.
- La memory est un petit graphe SQLite avec nœuds et arêtes typées.
- Les scopes sont `global` et `project`.
- Le contexte actif combine mémoire globale et mémoire du projet courant.
- La memory ne doit pas absorber automatiquement tous les messages, documents ou mails.
- La memory ne stocke pas de secrets bruts.
- La memory ne sert pas de permission implicite.
- Le kernel ne doit pas écrire librement dans la memory.
- Les écritures de dreaming doivent rester explicites, traçables et testables.

## Trace

- La trace raconte une exécution agentique observable.
- La trace relie intention, session, décision, action, observation et résultat.
- La trace garde les décisions sensibles du guardian.
- La trace masque les secrets et données sensibles.
- La trace ne devient pas une mémoire long terme.
- La trace ne remplace pas les logs techniques.
- La trace ne stocke pas le raisonnement privé complet du modèle.
- La trace doit rester lisible et peu bruyante.
- Un artefact référence une sortie structurée (`diff`, `tool_trace`, `image`, `report`, `file`, `screenshot`, `note`) sans imposer une UI.
- Un artefact `tool_trace` garde les tools utilisés, leur statut et un résumé court.
- Un artefact `diff` garde au minimum les fichiers touchés, les compteurs `+/-` et une référence au patch ou aux hunks.

## Logs

- Les logs diagnostiquent le runtime.
- Les logs utilisent la bibliothèque standard Python au départ.
- Les logs sont sobres par défaut.
- Les logs ne remplacent pas la trace.
- Les logs ne contiennent pas de secrets bruts.
- Le niveau de logs doit être configurable localement.

## Context Index

- Le context-index est une carte locale régénérable.
- Le context-index vit dans `.bb9/context-index.md` du workspace.
- Le context-index aide à s'orienter, il ne décide pas.
- Le context-index ne remplace pas la lecture ciblée, les tests ou la validation humaine.
- Le context-index ne devient pas une memory durable.
- Le context-index ne déclenche pas d'effets de bord.
- BB9 crée `.bb9/.gitignore` pour éviter de versionner cette mémoire locale.
- Le kernel reçoit le context-index comme contexte préparé ; il ne le construit pas.

## Goals

- Un goal est un état d'orchestration persistant, pas une note.
- Un goal vit dans `~/.bb9/goals/active.json`.
- Un goal boucle jusqu'à succès vérifié, blocage, pause, annulation ou limite.
- Un goal utilise la loop existante.
- Un goal ne contourne pas guardian, gateway ou hooks.
- Un goal enregistre ses itérations.
- Un goal exige une vérification concrète avant succès.
- Sans vérification exploitable, le goal se met en pause ou continue ; il ne se déclare pas atteint.
- Le worker de goal peut utiliser `subagents/goal`, puis `subagents/default`, puis l'agent courant.

## Tasks

- `tasks` est la persistance métier minimale, pas un planner et pas un scheduler.
- Le contrat d'usage vit dans `bb9/tools/tasks/TOOL.md` et `docs/tasks.md`.
- L'état runtime vit dans `~/.bb9/tasks/tasks.json`.
- Une tâche garde un titre lisible, un statut, une priorité, un agent cible, un projet éventuel et un historique court.
- Les statuts canoniques sont `backlog`, `queued`, `running`, `done`, `failed` et `paused`.
- `scheduled_for` est une échéance métier optionnelle, pas une autorisation d'exécution automatique.
- `CRON.md` déclenche ; `tasks` conserve le travail métier ; `.bb9/plan.md` décrit le plan courant.
- Le dreaming peut matérialiser `task.create` en tâche durable seulement lors de `/dream run` ou `/dream apply`.
- Créer ou modifier une tâche demande confirmation.
- Il n'y a pas de commande REPL `/tasks` : l'utilisateur parle naturellement et l'agent choisit le tool.
- Une tâche ne contient jamais de secret brut.
- Le tool `tasks` ne lance pas d'agent, ne notifie pas et ne remplace pas un dashboard.

## Cron

- Un cron est une archive `CRON.md`.
- Le même concept couvre `once` et `recurring`.
- `once` utilise `At: YYYY-MM-DD HH:MM`.
- `recurring` utilise `Time: HH:MM` et éventuellement `Days`.
- Sans `Days`, un cron récurrent est quotidien.
- Les jours canoniques sont en anglais ; `daily`, `weekdays` et `weekend` sont acceptés.
- La cadence vit dans `CRON.md`, pas dans le code métier.
- L'état runtime vit dans `~/.bb9/cron-state.json`, pas dans `CRON.md`.
- `Retry`, `Notification` et `History` restent déclaratifs.
- `/cron tick` déclenche seulement les crons dus.
- Un cron ne crée pas de daemon obligatoire.
- Un cron peut lancer une commande interne explicitement supportée, par exemple `/dream run <name>`.
- Un cron ne manipule pas directement `tasks` ; il déclenche une intention si une décision agentique est nécessaire.
- Une routine planifiée ne devient jamais une permission permanente implicite.

## Dream

- Un dream est une archive `~/.bb9/dreams/<name>/DREAM.md`.
- `DREAM.md` décrit quoi consolider, pas quand lancer.
- La cadence du dreaming appartient au cron ou à une commande explicite.
- Le dreaming lit memory, sessions, documents projet et contributions skills/tools.
- Le dreaming consolide, relie, corrige et propose.
- Le dreaming n'exécute pas de tool métier.
- Les actions produites par le dreaming restent proposées.
- `task.create` est la seule action dream matérialisable au départ, et elle crée une tâche sans l'exécuter.
- Les opérations mémoire attendues sont structurées en JSON.
- `/dream run` appelle le provider actif et applique les opérations mémoire.
- `/dream preview` crée un plan pending sans appliquer.
- `/dream apply` applique le plan pending.
- Le plan pending vit dans `~/.bb9/dream-pending.json`.
- `/dream run` et `/dream apply` produisent un rapport JSON et Markdown dans `~/.bb9/dreams/reports/`.
- `/dream run` et `/dream apply` peuvent écrire les actions `task.create` dans `~/.bb9/tasks/tasks.json`.
- Un rapport de dream est un artefact d'audit, pas une mémoire durable.
- `/dream reports` liste les rapports et `/dream report <id>` affiche le Markdown.
- Les `DREAM.md` de skills/tools sont des contributions, pas des cycles complets.

## Attachments Et Images

- Une image est référencée explicitement dans le texte.
- Les chemins image doivent rester sous `.bb9/uploads/` ou `.bb9/artifacts/screenshots/` du workspace.
- Le provider reçoit les images seulement s'il supporte les entrées multimodales.
- Les références image ne doivent pas ouvrir un accès libre au système de fichiers.

## Modèles Et Métadonnées

- Les métadonnées modèle servent au budget de contexte et à la compaction.
- Le cache utilisateur vit dans `~/.bb9/model-metadata.json`.
- Une table connue embarquée couvre les modèles courants.
- Un fallback prudent est utilisé si le modèle est inconnu.
- Aucune mise à jour web implicite ne doit être déclenchée par la compaction.

## Mode Continu Et Daemon

- Le mode continu doit être lancé explicitement.
- Le mode continu doit rester interrompable.
- Le daemon au démarrage est une option future, pas une condition d'usage.
- Le mode continu ne change pas les règles guardian/gateway.
- Aucun cron, dream ou goal ne doit obtenir une permission permanente implicite.

## Persistance Runtime

- `~/.bb9/providers.json` garde la config provider.
- `~/.bb9/settings.json` garde le profil courant.
- `~/.bb9/trusted-roots.md` garde les dossiers autorisés.
- `~/.bb9/secrets/` garde les secrets locaux.
- `~/.bb9/goals/active.json` garde le goal actif.
- `~/.bb9/cron-state.json` garde l'état des crons.
- `~/.bb9/sessions.db` garde les sessions récentes.
- `~/.bb9/visible-history.db` garde l'historique visible et les artefacts.
- `~/.bb9/memory.db` garde la mémoire durable SQL graph.
- `~/.bb9/tasks/tasks.json` garde la persistance métier minimale.
- `~/.bb9/dream-pending.json` garde le plan de dream en attente.
- `~/.bb9/dreams/reports/` garde les rapports de dream.
- `.bb9/context-index.md` garde l'index régénérable du workspace.

## Questions Encore Ouvertes

- Comment stabiliser les rapports de dream sans polluer la mémoire durable ?
- Quelle forme exacte donner à une trace persistée locale ?
- Comment brancher un mode continu fiable sans daemon implicite ?
- Quels workflows méritent une archive `WORKFLOW.md` réelle ?
