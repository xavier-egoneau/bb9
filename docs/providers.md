# Providers

## Intention

Définir comment le système parle aux modèles ou services externes sans dépendre d’un fournisseur unique.

Un provider est une source de génération, de raisonnement, d’embedding ou d’autre capacité externe.

Un provider peut aider à produire une décision ou une action structurée, mais il ne doit jamais appeler un tool directement.

Le kernel peut utiliser un provider via une interface abstraite. Les détails concrets restent dans l'adaptateur provider.

## Contrat

Les providers doivent :

- être déclarés explicitement ;
- exposer une interface minimale commune ;
- masquer les détails spécifiques derrière un adaptateur simple ;
- supporter d'abord un adapter OpenAI-compatible sans dépendance externe ;
- séparer registre, configuration locale, méthode d'authentification et modèle choisi ;
- permettre de récupérer une liste de modèles quand le provider l'expose simplement ;
- ne jamais exposer de secret ;
- retourner des erreurs exploitables.

Les providers ne doivent pas :

- contaminer le kernel avec leurs spécificités ;
- imposer un framework ;
- être nécessaires pour lire ou maintenir le projet ;
- contourner la loop, les hooks, le guardian ou le gateway ;
- rendre le système inutilisable sans réseau si une étape locale suffit.

## Questions à résoudre

- Quels providers OpenAI-compatible tester en premier après OpenAI et OpenRouter : Ollama, LM Studio, vLLM ?
- Interface minimale : `complete` seul au départ, ou `chat` rapidement ?
- Comment gérer les timeouts et erreurs réseau ?
- Faut-il un provider local obligatoire pour le mode minimal ?

## Forme retenue pour BB9

BB9 reprend l'idee de la brique `provider_config` de Marius, mais en plus petit :

- `bb9.providers` contient les adapters runtime.
- `bb9.core.provider_config` contient le registre, la config locale, les references de secrets et la recuperation des modeles.
- `/model` est l'entree utilisateur pour choisir ou ajouter un provider.
- La config utilisateur vit dans `~/.bb9/providers.json`.

Un provider configure contient :

- un nom local ;
- un type de provider (`openai`, `openrouter`, `openai-compatible`, `runbb9`, `local-runtime-sglang`, `local-runtime-llamacpp`, `ollama`, `ollama-cloud`) ;
- une methode d'authentification (`api` ou `web`) ;
- une URL de base ;
- une reference de secret (`env:NAME` ou `file:/path`) ;
- un modele actif.

Un agent ou subagent peut definir son provider et son modele effectifs avec `MODEL.md`. Cette selection réutilise une entrée provider déclarée, donc ses secrets et son auth, mais elle change bien le provider actif du run quand l'agent change.
`MODEL.md` peut aussi porter `ReasoningEffort`, transmis au provider quand il est renseigne.
Les surfaces doivent afficher ce couple effectif. Un agent `local` configuré sur
`ollama-local` + `qwen3:14b` ne doit pas rester présenté comme `ollama cloud` +
`minimax-m3`.

BB9 resout aussi des metadonnees de modele pour le budget de contexte :

- cache utilisateur dans `~/.bb9/model-metadata.json` ;
- table connue embarquee pour les modeles courants ;
- fallback prudent si le modele est inconnu.

Ces metadonnees servent notamment a l'auto-compaction : 90% de la fenetre de contexte du modele actif pour trim, 95% pour synthese, 98% pour reset, ou une limite souple d'entree quand le provider signale une zone couteuse.

Quand l'utilisateur change de provider ou de modele, BB9 doit resoudre aussitot
les metadonnees du nouveau modele actif et alimenter ce cache. Ainsi le runtime
ne redemande pas la meme information a chaque changement de modele ou relance de
session.

Le contexte structurel permanent de BB9 doit rester borne. Cible pratique :
system prompt, agent, index de tools/skills et regles generales ne devraient pas
depasser environ 10% de la fenetre de contexte connue. Si la fenetre est
inconnue, BB9 utilise le fallback prudent et doit signaler cette incertitude
quand elle devient importante pour la tache.

Le runtime ne fait pas de requete web implicite pour ces metadonnees. Une mise a jour web devra passer par une brique explicite et controlable, pas par l'auto-compaction.

Les secrets bruts ne doivent pas etre ecrits dans les fichiers Markdown du projet.

## Authentification

`api` est le chemin executable actuel :

- l'utilisateur choisit un provider ;
- BB9 propose l'URL de base ;
- BB9 demande une reference de secret, par defaut une variable d'environnement ;
- si l'utilisateur colle une cle brute dans ce champ, BB9 la stocke localement
  comme `secret:...` et n'affiche plus la valeur brute ;
- BB9 tente de lister les modeles via l'endpoint du provider ;
- l'utilisateur choisit un modele.

Pour Ollama local, choisir le provider `Ollama local`. L'URL par défaut est
`http://localhost:11434/v1` et aucune clé API n'est demandée.

Pour le service local runBB9, choisir le provider `runBB9 local`. L'URL par
défaut est `http://127.0.0.1:30999/v1` et aucune clé API n'est demandée. BB9
tente de démarrer `runbb9 serve` depuis `BB9_LOCAL_RUNTIME_ROOT` ou le dossier
sibling `../runtime` si `/v1/models` ne répond pas. `runBB9` liste tous les
modèles locaux qu'il sait router, puis démarre le backend spécialisé seulement
quand un modèle est réellement appelé.

Les anciens providers directs du projet `runtime` restent lisibles pour les
configs existantes, mais ne sont plus proposés comme nouveau choix dans l'UI.
Ils ne sont pas le chemin recommandé pour les nouvelles configurations. Ils
servaient à choisir directement selon le backend lancé :

- `Local Runtime SGLang`, URL par défaut `http://127.0.0.1:30000/v1`, pour les modèles agentic/planning comme `qwen3-14b-awq` ;
- `Local Runtime llama.cpp`, URL par défaut `http://127.0.0.1:8080/v1`, pour les modèles repo-edit comme `gemma4-e4b-gguf-q4km`.

Ces deux providers utilisent le même adapter OpenAI-compatible que les providers
distants, mais sans clé API. Le runtime peut être lancé séparément côté
`/home/egza/Documents/projets/runtime`, ou démarré automatiquement par BB9 si
l'endpoint ne répond pas.
Ils ne remplacent pas `Ollama local` : les deux modes peuvent coexister, y compris pour un même modèle si celui-ci est disponible à la fois dans Ollama et dans le runtime expérimental. Le choix se fait par l'entrée provider active ou par `ProviderId` dans `MODEL.md`.
Si un provider `local-runtime-*` est utilisé alors que son endpoint ne répond pas, BB9 tente de lancer automatiquement le runtime depuis `BB9_LOCAL_RUNTIME_ROOT` ou depuis le dossier sibling `../runtime`, puis réessaie la découverte des modèles ou l'appel chat. Les logs de lancement vivent dans `~/.bb9/local-runtime/`. Mettre `BB9_LOCAL_RUNTIME_AUTOSTART=0` désactive ce comportement.

Pour Ollama Cloud, choisir le provider `Ollama Cloud`. L'URL par défaut est
`https://ollama.com`, la clé attendue est `OLLAMA_API_KEY`, la liste des modèles
passe par `/api/tags` et la génération par `/api/chat`.

`web` existe comme forme de configuration pour tenir compte des abonnements type ChatGPT/Codex.
BB9 porte maintenant le minimum utile depuis Marius :

- lancement d'un flow OAuth local ;
- retour navigateur sur `http://localhost:1455/auth/callback` ;
- stockage local des tokens dans `~/.bb9/secrets/` ;
- choix de modele depuis le cache local Codex si disponible, ou saisie manuelle ;
- appel runtime via l'adapter ChatGPT-web.

Cette voie reste marquee comme experimentale : elle depend du flux ChatGPT/Codex, moins stable qu'une API OpenAI-compatible classique.
