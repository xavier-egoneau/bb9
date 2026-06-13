"""Shared REPL command declarations."""

from __future__ import annotations

NATIVE_REPL_COMMANDS = (
    ("/help", "afficher l'aide", True),
    ("/context", "afficher l'état courant", True),
    ("/history", "afficher l'historique visible", True),
    ("/new", "nouvelle session", True),
    ("/compact", "compacter le contexte court", True),
    ("/model-context", "définir la taille de la fenêtre de contexte du modèle actif", True),
    ("/project", "changer le workspace actif", True),
    ("/workspace", "changer le workspace actif", True),
    ("/tools", "lister ou activer les tools", True),
    ("/skills", "lister ou activer les skills", True),
    ("/model", "choisir provider et modèle", False),
    ("/goal", "objectif autonome", False),
    ("/cron", "routines et tâches planifiées", False),
    ("/dream", "consolidation mémoire", False),
    ("/profil", "changer le niveau de permission", False),
    ("/profile", "changer le niveau de permission", False),
    ("/exit", "quitter le REPL", False),
    ("/quit", "quitter le REPL", False),
)
