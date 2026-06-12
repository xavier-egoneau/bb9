# Agents

## Intention

Définir comment le système découvre et charge les agents internes du produit.

Un agent est une identité de travail utilisateur décrite en Markdown. Le kernel peut l'utiliser comme contexte, mais il ne doit pas coder cette identité en dur.

## Structure minimale

Un agent vit dans :

```text
~/.bb9/agents/<name>/
  IDENTITY.md
  SOUL.md
  MODEL.md
  TELEGRAM.md
  SKILLS_DISABLED.md
  TOOLS_DISABLED.md
  subagents/
    INDEX.md
    default/
```

- `IDENTITY.md` décrit le rôle, le périmètre et les responsabilités.
- `SOUL.md` décrit la posture, les préférences de travail et les limites.
- `MODEL.md` peut surcharger le modèle utilisé par cet agent, en gardant le provider/auth actif.
- `TELEGRAM.md` configure le channel Telegram de cet agent, sans contenir le token brut.
- `SKILLS_DISABLED.md` désactive certains skills globaux pour cet agent.
- `TOOLS_DISABLED.md` désactive certains tools globaux pour cet agent.
- `subagents/` contient les subagents locaux de cet agent, s'il y en a.
- `subagents/default/` sert de fallback pour les delegations bornees sans specialisation claire.
- `subagents/INDEX.md` est genere depuis les subagents disponibles.

Ces noms sont volontairement explicites. Ils peuvent évoluer si l'usage réel montre une meilleure forme.

## Contrat

Les agents doivent :

- être découverts depuis un dossier racine explicite ;
- vivre par défaut dans le dossier user `~/.bb9/agents/` ;
- avoir un nom stable ;
- rester lisibles en Markdown ;
- pouvoir être chargés comme contexte du kernel ;
- recevoir tous les skills par défaut, sauf ceux explicitement désactivés ;
- recevoir tous les tools par défaut, sauf ceux explicitement désactivés ;
- ne pas contenir de secrets ;
- rester séparés de `AGENTS.md`, qui concerne les contributeurs du dépôt.

Les agents ne doivent pas :

- exécuter directement des actions ;
- contourner le guardian ;
- posséder la mémoire durable ;
- imposer un provider ou un channel ;
- devenir une collection de prompts opaques.

## Templates

Le repo BB9 peut livrer des templates dans :

```text
bb9/templates/agents/<name>/
```

L'installation les copie dans `~/.bb9/agents/` seulement s'ils sont absents. Le dossier user devient ensuite la source active.

Le skill utilisateur template `agent-factory` aide à concevoir et créer des
agents ou subagents Markdown sans déplacer cette logique dans le coeur Python.
Il expose notamment `/create-agent` et `/create-subagent` comme méthodes
Markdown.

## Découverte

La découverte minimale consiste à lister les sous-dossiers de `~/.bb9/agents/` qui contiennent au moins `IDENTITY.md` ou `SOUL.md`.

Le kernel reçoit un agent déjà chargé. Il ne parcourt pas lui-même le disque.

Le runtime peut aussi generer et injecter l'index des subagents de l'agent parent. Cet index aide le parent a savoir quels workers specialises existent sans charger tous leurs fichiers complets.

## Modèle

`MODEL.md` reste volontairement minimal :

```md
# Model

Model : gpt-5-mini
ReasoningEffort : low
```

S'il est vide, l'agent utilise le modèle actif du provider courant. Pour un subagent, `MODEL.md` hérite du parent s'il est absent ou vide.

Cette forme permet notamment de donner un modèle léger à un worker spécialisé comme `subagents/default/` ou `subagents/research/`, sans dupliquer les secrets ni la configuration provider.

`ReasoningEffort` est optionnel et dépend du provider. Pour les modèles OpenAI récents de type GPT-5.x, les valeurs utiles sont :

```text
none
low
medium
high
xhigh
```

S'il est vide, il hérite du parent pour un subagent ou laisse le provider appliquer son défaut.

## Telegram

Telegram est une configuration de channel attachée à l'agent. Elle vit dans :

```md
# Telegram

## Activation

active

## Token

secret:TELEGRAM_DEFAULT_BOT_TOKEN

## AllowedChatIds

[123456789]
```

Le token doit toujours être une référence `secret:`, `env:` ou `file:`. L'interface web peut recevoir un token brut, mais elle le transforme en secret local avant d'écrire `TELEGRAM.md`.

Quand `bb9 web` tourne, le runtime Telegram de l'agent actif démarre
automatiquement si cette configuration est active. Il peut aussi se lancer avec
`bb9 telegram` pour diagnostic ou usage hors web. Il lit cette configuration,
résout le token, filtre les chats autorisés et route les messages vers l'accueil
de l'agent. `AllowedChatIds` est donc une autorisation effective, pas seulement
une indication d'interface.

## Influence runtime

`SOUL.md` doit influencer le comportement, pas seulement le ton.

Le kernel peut en dériver un contrat comportemental court envoyé au provider avec le contexte actif. La loop peut aussi en tenir compte pour ajuster l'autonomie d'exploration dans les limites du profil de permission.

Cette influence reste bornée :

- `SOUL.md` n'autorise jamais à contourner le guardian ;
- les secrets, actions hors workspace, suppressions durables et actions extérieures restent soumis aux règles de permission ;
- le fichier source reste la référence lisible, le contrat runtime n'est qu'une projection courte.

## Questions à résoudre

- `SOUL.md` est-il le bon nom durable ?
- Quels fichiers sont obligatoires ?
- Comment choisir l'agent par défaut ?
- Comment gérer plusieurs agents sans multi-agent prématuré ?
- Comment empêcher une identité agent d'inclure des instructions dangereuses ?
- Comment signaler clairement les skills désactivés ?
- Comment signaler clairement les tools désactivés ?
