---
name: project-explorer
description: Explorer un projet local avec des commandes de lecture et produire une synthèse courte.
---

# Project Explorer

## Résumé

Explorer un projet local avec des commandes de lecture et produire une synthèse courte.

## Intention

Explorer un projet local de manière progressive et lisible.

Ce tool est documentaire : il ajoute un comportement attendu et s'appuie sur d'autres tools comme `shell` pour lire le workspace.

## Quand l'utiliser

- Un projet vient d'être ouvert.
- Le contexte Markdown est absent ou incomplet.
- Le système doit comprendre une structure existante avant d'agir.

## Méthode

1. Identifier le workspace courant.
2. Lister les fichiers avec une commande de lecture.
3. Chercher les fichiers de gouvernance : `README.md`, `ROADMAP.md`, `DECISIONS.md`, `MEMORY.md`, `AGENTS.md`.
4. Repérer les fichiers de dépendances et de lancement.
5. Lire seulement les fichiers utiles.
6. Résumer ce qui existe, ce qui manque et les questions ouvertes.

## Tools typiques

- `shell`

## Commandes

- `/explore` : explorer le workspace courant et produire une synthèse courte.

## Limites

- Ne pas créer de fichier.
- Ne pas modifier le projet.
- Ne pas deviner une architecture complète à partir d'une seule commande.
- Ne pas remplacer la lecture ciblée des fichiers importants.
