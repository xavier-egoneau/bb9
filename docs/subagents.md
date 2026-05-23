# Subagents

## Intention

Prendre en compte les subagents dès la conception sans construire trop tôt un système multi-agent complexe.

Un subagent est une unité de travail déléguée : mission bornée, contexte réduit, droits explicites et résultat synthétique.

## Contrat

Les subagents doivent :

- avoir un nom et une responsabilité claire ;
- recevoir une intention déléguée et un contexte minimal ;
- déclarer leurs tools, skills et permissions autorisées ;
- passer par le guardian avant toute action sensible ;
- retourner une observation ou synthèse exploitable par la loop principale ;
- pouvoir être désactivés ou ignorés sans casser la boucle simple.

Les subagents ne doivent pas :

- remplacer la loop principale ;
- posséder la mémoire durable ;
- contourner le gateway ;
- contourner les hooks ou le guardian ;
- lancer d'autres subagents sans règle explicite ;
- devenir une excuse pour créer une architecture prématurée.

## Position provisoire

Le projet prévoit les subagents dans les contrats dès le départ, mais l'implémentation initiale reste mono-agent.

Un subagent peut vivre dans le dossier de son agent parent :

```text
~/.bb9/agents/<agent>/subagents/<subagent>/
  IDENTITY.md
  SOUL.md
  MODEL.md
  SKILLS_DISABLED.md
  TOOLS_DISABLED.md
```

Le subagent `default` est le fallback attendu quand une tache doit etre deleguee mais qu'aucune specialisation ne correspond clairement. Il n'est pas l'agent parent bis : il sert a isoler une mission bornee dans un contexte separe.

S'il n'a pas de dossier, il n'existe pas comme subagent configuré.

S'il a un dossier mais qu'un fichier manque :

- `IDENTITY.md` hérite de l'agent parent ;
- `SOUL.md` hérite de l'agent parent ;
- `MODEL.md` hérite de l'agent parent ;
- `SKILLS_DISABLED.md` s'ajoute aux skills désactivés du parent ;
- `TOOLS_DISABLED.md` s'ajoute aux tools désactivés du parent.

Cet héritage garde les subagents légers et permet de spécialiser seulement ce qui change.

## Index

Le runtime genere un index court dans :

```text
~/.bb9/agents/<agent>/subagents/INDEX.md
```

Cet index liste les subagents disponibles et leur usage principal, extrait de `IDENTITY.md`. Il est injecte dans le contexte du parent pour eviter un choix au hasard.

La convention minimale est :

- `default` : worker generique quand aucune specialisation ne colle mieux ;
- `goal` : worker utilise par `/goal` pour faire avancer une iteration sans valider le succes final ;
- un subagent specialise doit decrire `Quand l'utiliser` dans `IDENTITY.md`.

`MODEL.md` permet d'optimiser un subagent avec un modele plus leger tout en gardant le provider et l'authentification actifs. Exemple pour `subagents/goal/MODEL.md` :

```md
# Model

Model : gpt-5-mini
ReasoningEffort : low
```

La première forme acceptable pourrait être une délégation interne très simple :

```text
intention principale -> décision -> tâche déléguée -> résultat borné -> reprise par la loop principale
```

## Questions à résoudre

- Quelle forme minimale donner à une tâche déléguée ?
- Un subagent est-il une configuration, un skill spécialisé, ou une loop isolée ?
- Comment limiter son contexte, ses tools et son nombre d'itérations ?
- Comment tracer une délégation sans rendre la trace illisible ?
- Quand un subagent a-t-il le droit de demander une validation utilisateur ?
- Quels subagents réels justifient une première implémentation ?
