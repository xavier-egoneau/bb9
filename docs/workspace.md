# Workspace

## Intention

Définir le périmètre local dans lequel une tâche agentique peut lire, écrire et exécuter des actions.

Un workspace sert de frontière pratique pour limiter les effets de bord et rendre les runs inspectables.

Un trusted root est un autre dossier autorisé durablement par l'utilisateur comme zone de travail.

## Contrat

Le workspace doit :

- définir un répertoire racine explicite ;
- limiter par défaut les actions fichiers et shell au workspace et aux trusted roots ;
- pouvoir être rattaché à une session, une trace et un run ;
- permettre d'isoler une expérimentation ou une délégation ;
- rendre les changements inspectables avant intégration.

Le workspace ne doit pas :

- donner une permission globale sur la machine ;
- masquer les actions hors périmètre ;
- remplacer le guardian ;
- imposer un dashboard ou un orchestrateur lourd ;
- rendre le système inutilisable sans Git.

## Position provisoire

Le workspace fait partie du socle minimal.

La phase 1 peut utiliser le dépôt courant comme workspace simple, mais chaque action fichier ou shell doit connaître ce périmètre.

Dans le workspace ou un trusted root, l'écriture normale est autorisée. Les actions sensibles restent soumises au guardian.

Plus tard, une tâche sensible ou parallèle peut être lancée dans un workspace isolé, par exemple un worktree Git, pour comparer, tester et fusionner seulement ce qui est utile.

## Vocabulaire local

- `repo` : le dépôt BB9, où vivent le runtime et les tools natifs.
- `dossier user` : `~/.bb9/`, où vivent les choix privés et persistants de l'utilisateur.
- `workspace` : le dossier dans lequel BB9 est lancé pour travailler.
- `trusted root` : workspace ou dossier hors workspace autorisé durablement par l'utilisateur.

## Trusted roots

Les trusted roots persistants vivent dans `~/.bb9/trusted-roots.md`, dans le dossier user.

Ce fichier n'appartient pas au workspace. Un workspace ne doit pas pouvoir s'accorder lui-même des permissions globales.

## Scripts

Des scripts de préparation, lancement ou nettoyage peuvent exister plus tard :

- setup ;
- run ;
- teardown.

Ils doivent être explicites, traçables et soumis au guardian quand ils déclenchent des effets de bord.

## Questions à résoudre

- Le workspace est-il toujours un répertoire local ?
- Comment représenter un run isolé ?
- Quand utiliser un worktree plutôt que le dépôt courant ?
- Comment comparer et intégrer les changements ?
- Comment demander une permission pour sortir du workspace ?
