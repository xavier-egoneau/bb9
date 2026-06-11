---
name: project-onboarding
description: Vérifier ou installer un contexte de gouvernance minimal dans un projet.
---

# Project Onboarding

## Résumé

Vérifier ou installer un contexte de gouvernance minimal dans un projet.

## Intention

Entrer dans un projet en installant un contexte de travail clair, sans sur-structurer.

Ce tool est documentaire : il ajoute un comportement attendu. La création ou modification de fichiers reste une action séparée soumise au guardian.

## Quand l'utiliser

- Le système arrive dans un projet inconnu.
- Les fichiers de gouvernance sont absents.
- L'utilisateur demande de structurer un projet.

## Méthode

1. Utiliser `project-explorer` pour comprendre l'existant.
2. Vérifier si `README.md`, `ROADMAP.md`, `DECISIONS.md` et `MEMORY.md` existent.
3. Si le contexte existe, le lire et l'utiliser.
4. Si le contexte manque, proposer une structure minimale.
5. Demander validation avant toute création ou modification durable.

## Sortie attendue

- Synthèse courte du projet.
- Fichiers de contexte présents.
- Fichiers de contexte manquants.
- Proposition d'action claire si une création est nécessaire.

## Limites

- Ne pas créer automatiquement une gouvernance.
- Ne pas ajouter de framework.
- Ne pas transformer l'exploration en décision durable sans validation.
