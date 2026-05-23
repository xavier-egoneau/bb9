# AGENTS.md

Consignes pour tout agent IA ou humain assisté par IA travaillant sur ce dépôt.

Ce fichier décrit les règles des contributeurs au dépôt. Il ne décrit pas les agents internes du produit.

## Intention du projet

Ce projet vise à construire un système agentique élégant, minimal et compréhensible.

La priorité est :

1. simplicité ;
2. lisibilité ;
3. contrôle humain ;
4. traçabilité ;
5. extensibilité seulement si nécessaire.

## Lignes directrices

- Markdown sert à penser, cadrer, décider, documenter et conserver la mémoire projet.
- Python sert à agir, parser, appeler, vérifier, exposer et tracer.
- Nommer les concepts tôt est acceptable ; les implémenter trop tôt ne l'est pas.
- Garder le système compréhensible en dix minutes doit rester une contrainte forte.
- Toute nouvelle brique doit avoir une responsabilité nette.
- Toute abstraction doit être justifiée par un usage réel, pas par anticipation.
- Les décisions durables doivent être écrites dans `DECISIONS.md`.
- Les faits projet durables doivent être écrits dans `MEMORY.md`.

## Règles de travail

- Lire `README.md`, `ROADMAP.md`, `DECISIONS.md` et `MEMORY.md` avant toute modification structurante.
- Lire le document concerné dans `docs/` avant de modifier une brique système.
- Ne pas ajouter de framework agentique lourd sans décision explicite dans `DECISIONS.md`.
- Préférer peu de fichiers bien nommés à une architecture prématurée.
- Ne pas créer d’abstraction avant d’avoir au moins deux usages réels.
- Documenter toute décision durable dans `DECISIONS.md`.
- Mettre à jour `ROADMAP.md` si une étape change.
- Mettre à jour `MEMORY.md` uniquement pour les faits projet durables.
- Garder `README.md` orienté utilisateur : vision, usage, lancement.
- Garder les contrats de conception dans `docs/`.

## Responsabilités à respecter

- Le kernel décide, mais n'exécute pas directement les effets de bord.
- La loop orchestre le cycle agentique.
- Le gateway exécute les actions concrètes et retourne des observations.
- Le guardian autorise, demande confirmation ou bloque.
- La session porte le contexte court ; elle ne doit pas être confondue avec le gateway.

## Style attendu

- Minimal.
- Direct.
- Pas de boilerplate inutile.
- Pas de sur-ingénierie.
- Chaque fichier doit avoir une raison claire d’exister.

## Interdits provisoires

- Pas de LangChain, AutoGen, CrewAI ou équivalent sans justification forte.
- Pas de base vectorielle au départ.
- Pas de dashboard avant d’avoir une boucle agentique utile.
- Pas de multi-agent avant d’avoir un agent simple qui fonctionne.
- Pas de structure package complexe avant nécessité.

## Définition de fait

Une modification est acceptable si :

- elle sert directement l’objectif du projet ;
- elle reste compréhensible rapidement ;
- elle n’introduit pas de dépendance inutile ;
- elle met à jour la documentation concernée ;
- elle ne rend pas le système plus opaque.
