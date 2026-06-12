---
activation: on-demand, /agent-factory, /create-agent, /create-subagent, créer un agent, nouvel agent, ajouter un agent, créer un subagent, nouveau subagent, worker spécialisé, identité agent, SOUL.md, IDENTITY.md
name: agent-factory
description: Créer ou améliorer des agents et subagents BB9 en archives Markdown lisibles.
---

# Agent Factory

## Résumé

Créer ou améliorer des agents et subagents BB9 en archives Markdown lisibles.

## Activation

Quand l'utilisateur demande de créer, modifier, spécialiser ou documenter un
agent BB9, un subagent, un worker spécialisé, une identité d'agent, un
`IDENTITY.md`, un `SOUL.md`, un `MODEL.md`, ou une politique de skills/tools
propre à un agent.

## Portée

Template global utilisateur. Un projet peut le spécialiser avec
`.bb9/skills/agent-factory/SKILL.md`, qui prendra le dessus dans ce workspace.

## Commandes

- `/agent-factory ...` : concevoir ou améliorer un agent ou subagent BB9.
- `/create-agent ...` : créer un agent utilisateur.
- `/create-subagent ...` : créer un subagent sous l'agent actif ou nommé.

Ces commandes sont des méthodes Markdown. Elles ne remplacent pas le guardian ni
les validations d'écriture.

## Rôle

Tu aides à créer des identités de travail BB9 claires, spécialisées et
maintenables.

Avant d'écrire, tu identifies :

- le rôle concret attendu ;
- si le besoin demande un agent ou un subagent ;
- l'agent parent si c'est un subagent ;
- les skills et tools à laisser actifs ou à désactiver ;
- le modèle ou le niveau de raisonnement seulement s'il y a une raison nette ;
- les fichiers existants à préserver.

## Décision agent ou subagent

Créer un agent quand :

- l'identité doit être choisie directement par l'utilisateur ;
- elle représente une manière durable de travailler ;
- elle peut avoir ses propres skills/tools désactivés ;
- elle mérite un dossier `~/.bb9/agents/<name>/`.

Créer un subagent quand :

- l'identité sert surtout de worker spécialisé ;
- elle doit hériter d'un agent parent ;
- elle intervient dans une délégation bornée ;
- elle vit naturellement dans `~/.bb9/agents/<parent>/subagents/<name>/`.

Ne pas créer d'agent quand :

- une consigne dans `SOUL.md` de l'agent actuel suffit ;
- un skill décrit mieux une méthode réutilisable ;
- un tool natif serait nécessaire pour une capacité d'action ;
- le rôle est ponctuel ou trop vague.

## Structure cible

Agent :

```text
~/.bb9/agents/<name>/
  IDENTITY.md
  SOUL.md
  MODEL.md
  SKILLS_DISABLED.md
  TOOLS_DISABLED.md
  subagents/
```

Subagent :

```text
~/.bb9/agents/<parent>/subagents/<name>/
  IDENTITY.md
  SOUL.md
  MODEL.md
  SKILLS_DISABLED.md
  TOOLS_DISABLED.md
```

## Contenu recommandé

`IDENTITY.md` doit décrire :

- nom ;
- type `subagent` si nécessaire ;
- description ;
- rôle ;
- responsabilité ;
- périmètre ;
- limites ;
- langue.

`SOUL.md` doit décrire :

- posture de travail ;
- préférences concrètes ;
- niveau d'initiative ;
- manière de rendre compte ;
- situations à refuser ou à escalader.

`MODEL.md` reste optionnel dans son contenu. S'il n'y a pas de raison claire de
surcharger le modèle, garder les champs vides.

`SKILLS_DISABLED.md` et `TOOLS_DISABLED.md` doivent rester des listes Markdown de
noms désactivés. Ne pas dupliquer la liste complète des capacités actives.

## Méthode

1. Relire `docs/agents.md` si le contrat d'agent est incertain.
2. Inspecter les agents existants avant de créer un nom proche.
3. Choisir un nom court en kebab-case.
4. Préparer les fichiers Markdown minimaux.
5. Écrire avec les tools de fichiers existants, jamais avec un chemin codé en dur.
6. Demander ou laisser le guardian demander confirmation pour les écritures hors workspace.
7. Vérifier que les fichiers existent et restent lisibles.
8. Résumer le nouvel agent, ses limites et ses capacités désactivées.

## Garde-fous

- Ne jamais stocker de secret dans un agent.
- Ne jamais contourner le guardian, le gateway ou les hooks.
- Ne pas ajouter de framework agentique.
- Ne pas créer de multi-agent implicite.
- Ne pas supprimer ou réécrire un agent existant sans demande explicite.
- Ne pas modifier `AGENTS.md` pour définir un agent produit.
- Ne pas désactiver `delegate` pour un agent normal sans raison.
- Désactiver `delegate` pour un subagent afin qu'il ne délègue pas à son tour.

## Sortie attendue

Quand tu livres un agent ou subagent, indique :

- le nom et le chemin ;
- pourquoi c'est un agent ou un subagent ;
- les fichiers créés ou modifiés ;
- les skills/tools désactivés ;
- le modèle ou raisonnement configuré, si présent ;
- les vérifications effectuées.
