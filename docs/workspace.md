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

## Etat technique courant

BB9 peut construire un `workspace-status` volatil au moment d'un run.

Cet état peut indiquer :

- le root effectif ;
- la branche Git et l'état propre ou modifié ;
- le package manager détecté ;
- les scripts utiles déclarés ;
- les fichiers de gouvernance présents ;
- la fraîcheur du context-index.

Cet état est injecté dans le `RunContext` et peut donc servir à toutes les surfaces : CLI, web local ou futur adapter externe.

Il ne doit pas :

- être traité comme une mémoire durable ;
- remplacer le context-index ;
- prétendre que les fichiers listés ont été lus ;
- déclencher des actions lourdes ou des effets de bord.

La distinction reste importante : le `workspace-status` dit ce que BB9 sait déjà de l'état local, le `context-index` donne une carte régénérable, et la lecture ciblée reste nécessaire avant une modification précise.

## Changement de workspace

Le changement de workspace est une primitive commune du runtime. Il n'appartient
pas à Telegram, au web ou au CLI.

Un channel peut transformer une demande utilisateur comme :

```text
mets-toi sur le projet tests et fais une critique
```

en deux opérations :

- résoudre `tests` vers un path local connu, proche ou explicite ;
- exécuter `fais une critique` avec ce path comme workspace.

Cette opération ne change pas forcément la session conversationnelle. Par
exemple, Telegram garde l'accueil de l'agent comme session visible, mais le run
peut utiliser le workspace demandé. Le web, lui, passe par son switch projet
existant afin de garder projet actif, cwd serveur, sessions projet et UI alignés.

Quand BB9 est lancé depuis un dossier trop large, par exemple le dossier
utilisateur ou la racine système, les surfaces doivent afficher une alerte non
bloquante. L'utilisateur peut alors relancer BB9 depuis un projet ou demander un
switch explicite vers un projet.

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
