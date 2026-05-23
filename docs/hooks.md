# Hooks

## Intention

Définir les points de contrôle avant et après exécution d'une action.

Les hooks permettent d'arrêter une action hors périmètre avant qu'elle touche un tool, puis de vérifier l'observation avant qu'elle revienne dans la session, la trace ou le modèle.

## Contrat

Les hooks doivent :

- être placés sur le chemin obligatoire entre décision et tool ;
- préparer l'appel au guardian avant toute exécution ;
- permettre au guardian de bloquer, demander confirmation ou laisser passer une action ;
- masquer les secrets et données sensibles après exécution ;
- produire des événements utiles pour la trace ;
- rester simples, explicites et auditables.

Les hooks ne doivent pas :

- devenir un workflow engine caché ;
- décider de l'objectif utilisateur ;
- exécuter eux-mêmes les effets de bord ;
- permettre au modèle de contourner le guardian ;
- modifier silencieusement une action sensible.

## Implémentation minimale

Au départ, les hooks ne doivent pas devenir un système de plugins.

Deux fonctions explicites suffisent :

```text
before_action(action, context) -> action_review
after_action(observation, context) -> observation
```

Ces fonctions peuvent appliquer des règles écrites en Markdown ou en Python simple. Elles restent appelées par la loop.

## Chemin obligatoire

```text
source de décision
-> pre-action hook
-> guardian decision
-> gateway
-> tool
-> post-action hook
-> observation
-> session / trace / kernel
```

Le modèle peut proposer une action structurée, mais il ne doit jamais appeler un tool directement.

## Pre-action hook

Le hook avant action vérifie notamment :

- l'action demandée ;
- les entrées ;
- le périmètre de travail ;
- les permissions nécessaires ;
- le risque ;
- la nécessité d'une confirmation utilisateur.

Le pre-action hook prépare la décision du guardian. Le guardian reste l'autorité qui autorise, demande confirmation ou bloque avant exécution.

Décisions possibles du guardian après le pre-action hook :

- `allow` : l'action peut passer au gateway ;
- `confirm` : l'action attend une validation humaine ;
- `block` : l'action est refusée ;
- `revise` : l'action doit être reformulée avant nouvel examen.

## Post-action hook

Le hook après action vérifie notamment :

- l'observation retournée ;
- les erreurs ;
- les secrets ou données sensibles à masquer ;
- la cohérence avec l'action autorisée ;
- les événements à transmettre à la trace.

Le post-action hook intervient après le tool. Il ne remplace pas le guardian et ne transforme pas une action interdite en action autorisée. Il sécurise seulement le retour d'exécution.

## Questions à résoudre

- Les hooks sont-ils des fonctions Python fixes, des règles Markdown, ou les deux ?
- Où vit la table des règles du guardian ?
- Quel format donner à une `guardian decision` ?
- Comment éviter qu'un hook modifie trop fortement une action ?
- Que doit voir le modèle quand une action est bloquée ?
