# Create Skill

## Résumé

Aider l'agent à concevoir et créer des skills utilisateur BB9 portables.

## Intention

Créer des skills utilisateur solides, lisibles et copiables d'un BB9 à un autre.

Ce tool sert surtout de guide de conception pour les skills. Il peut aussi générer un squelette minimal dans `~/.bb9/skills/`.

## Quand l'utiliser

- L'utilisateur veut créer un nouveau skill.
- L'utilisateur veut transformer une méthode de travail en extension réutilisable.
- L'agent veut ajouter une commande REPL utilisateur.
- L'agent veut documenter comment utiliser des tools existants dans un comportement durable.
- L'utilisateur demande de rendre une façon de travailler portable.

## Frontière skill / tool

Un skill utilisateur vit dans :

```text
~/.bb9/skills/<name>/SKILL.md
```

Un tool natif vit dans l'archive BB9 :

```text
bb9/tools/<name>/TOOL.md
```

Choisir un skill quand :

- la brique personnalise la façon de travailler de l'utilisateur ;
- elle doit être copiable entre installations BB9 ;
- elle compose ou oriente des tools existants ;
- elle ajoute une commande REPL locale utile à l'utilisateur.

Choisir un tool quand :

- la brique doit être livrée avec BB9 ;
- elle expose une capacité native commune ;
- elle touche le monde via une action concrète réutilisable par tous.

## Structure d'un skill

Structure minimale :

```text
~/.bb9/skills/<name>/SKILL.md
```

Structure avec extension REPL :

```text
~/.bb9/skills/<name>/SKILL.md
~/.bb9/skills/<name>/cli.py
```

`SKILL.md` porte l'intention, les règles, les limites, les tools à utiliser et les exemples.

`cli.py` est optionnel. S'il existe et expose `register(cli)`, BB9 peut l'appeler au démarrage du REPL.

## Sections recommandées

Un bon `SKILL.md` contient :

- `Résumé` : une phrase courte.
- `Activation` : `always`, `on-demand` ou une condition claire.
- `Intention` : le résultat recherché.
- `Quand l'utiliser` : signaux concrets.
- `Comportement attendu` : règles de décision.
- `Tools utilisés` : tools BB9 à privilégier.
- `Commandes REPL` : commandes ajoutées si `cli.py` existe.
- `Actions` : actions proposées et protocole si nécessaire.
- `Permissions` : ce qui est `allow`, `ask` ou interdit.
- `Secrets` : références attendues, jamais les valeurs.
- `Portabilité` : chemins relatifs, pas de chemin machine en dur.
- `Tests manuels` : commandes simples pour vérifier.

## Bonnes pratiques

- Commencer par Markdown seul si possible.
- Ajouter `cli.py` seulement si l'utilisateur a besoin d'une commande REPL réelle.
- Utiliser les tools existants avant de créer une nouvelle action.
- Ne jamais stocker de secret dans le skill.
- Utiliser des références comme `secret:NOM`.
- Ne pas coder de chemin absolu utilisateur.
- Ne pas dupliquer une méthode déjà portée par un tool natif.
- Garder un nom court, stable et lisible.
- Écrire pour un autre BB9 : le skill doit rester compréhensible hors de ce projet.

## Extension REPL

Un skill peut ajouter des commandes REPL avec `cli.py` :

```python
def register(cli):
    cli.add_command("/ma-commande", lambda rest: _run(cli, rest), "description courte")


def _run(cli, rest):
    print("ok")
    return True
```

Un skill peut aussi enregistrer :

- un intercepteur d'entrée utilisateur ;
- un handler de validation guardian ;
- une ligne affichée dans `/context` ;
- une capture locale temporaire.

Le CLI reste un hôte générique. Le skill ne doit pas modifier `bb9/core/cli.py`.

## Actions

Un skill peut proposer des actions, mais elles doivent rester contrôlées :

- préférer les tools existants et le protocole `BB9_ACTION <tool> ...` ;
- demander validation humaine pour toute écriture durable ;
- ne jamais contourner guardian, gateway ou hooks ;
- documenter le protocole et les risques dans `SKILL.md`.

Si une action devient générique et stable, envisager un tool natif ou un tool compagnon plutôt que de cacher trop de logique dans un skill.

## Protocole

```text
BB9_ACTION create_skill draft <nom>
BB9_ACTION create_skill draft <nom> cli
```

## Sortie

`draft <nom>` crée un squelette `SKILL.md`.

`draft <nom> cli` crée aussi un squelette `cli.py`.

## Permissions

Créer un skill écrit dans `~/.bb9/skills/`. Cette action demande toujours confirmation.

Le tool ne remplace pas la validation humaine : il prépare une base lisible que l'utilisateur peut relire et adapter.
