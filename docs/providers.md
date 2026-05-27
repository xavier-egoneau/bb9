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
- un type de provider (`openai`, `openrouter`, `openai-compatible`, `ollama`, `ollama-cloud`) ;
- une methode d'authentification (`api` ou `web`) ;
- une URL de base ;
- une reference de secret (`env:NAME` ou `file:/path`) ;
- un modele actif.

Un agent ou subagent peut surcharger ce modele avec `MODEL.md`. Cette surcharge ne change pas le provider ni l'authentification ; elle sert surtout a faire tourner certains workers, comme `subagents/goal`, sur un modele plus leger.
`MODEL.md` peut aussi porter `ReasoningEffort`, transmis au provider quand il est renseigne.

BB9 resout aussi des metadonnees de modele pour le budget de contexte :

- cache utilisateur dans `~/.bb9/model-metadata.json` ;
- table connue embarquee pour les modeles courants ;
- fallback prudent si le modele est inconnu.

Ces metadonnees servent notamment a l'auto-compaction : environ 80% de la fenetre de contexte du modele actif, ou une limite souple d'entree quand le provider signale une zone couteuse.

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
