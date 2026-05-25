# Create Skill

## Résumé

Aider l'agent à concevoir et créer des skills utilisateur BB9 portables.

## Intention

Créer des skills utilisateur solides, lisibles et copiables d'un BB9 à un autre.

Ce tool sert surtout de guide de conception pour les skills. Il peut aussi générer un squelette minimal dans `~/.bb9/skills/` ou dans `.bb9/skills/` du workspace courant.

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
.bb9/skills/<name>/SKILL.md
```

`~/.bb9/skills/` contient les skills globaux. `.bb9/skills/` contient les skills locaux au projet courant. À nom égal, le skill local prend le dessus.

Un tool natif vit dans l'archive BB9 :

```text
bb9/tools/<name>/TOOL.md
```

Choisir un skill quand :

- la brique personnalise la façon de travailler de l'utilisateur ;
- elle doit être copiable entre installations BB9 ;
- elle compose ou oriente des tools ou skills existants ;
- elle ajoute une commande REPL locale utile à l'utilisateur ;
- elle ajoute une action utilisateur partageable.

Choisir un tool quand :

- la brique doit être livrée avec BB9 ;
- elle expose une capacité native commune ;
- elle touche le monde via une action concrète réutilisable par tous les BB9.

## Structure d'un skill

Structure minimale :

```text
~/.bb9/skills/<name>/SKILL.md
.bb9/skills/<name>/SKILL.md
```

Structure avec fichiers Python optionnels :

```text
~/.bb9/skills/<name>/SKILL.md
~/.bb9/skills/<name>/cli.py
~/.bb9/skills/<name>/runtime.py
~/.bb9/skills/<name>/core.py
~/.bb9/skills/<name>/DREAM.md
```

La même structure est valide sous `.bb9/skills/<name>/` pour un skill local au workspace.

`SKILL.md` porte l'intention, les règles, les limites, les tools à utiliser et les exemples.

`cli.py` est l'entrée REPL optionnelle. `runtime.py` est l'entrée action optionnelle. `core.py` est un backend optionnel importé par `cli.py` ou `runtime.py`.

Les commandes d'un skill appartiennent au skill. Elles doivent être déclarées dans `SKILL.md` et enregistrées par `cli.py` seulement si une intégration REPL réelle est nécessaire.

## Sections recommandées

Un bon `SKILL.md` contient :

- `Résumé` : une phrase courte.
- `Activation` : `always`, `on-demand` ou une condition claire.
- `Intention` : le résultat recherché.
- `Quand l'utiliser` : signaux concrets.
- `Comportement attendu` : règles de décision.
- `Briques utilisées` : tools ou skills BB9 à privilégier.
- `Commandes` : commandes slash portées par le skill, Markdown pur ou via `cli.py`.
- `Actions` : actions proposées et protocole si nécessaire.
- `Permissions` : ce qui est `allow`, `ask` ou interdit.
- `Secrets` : références attendues, jamais les valeurs.
- `Portabilité` : chemins relatifs, pas de chemin machine en dur.
- `Tests manuels` : commandes simples pour vérifier.

Convention recommandée pour les nouveaux skills :

- `/<skill>` pour la commande principale ;
- `/<skill>-<commande>` pour les variantes ;
- éviter les alias courts non namespacés comme `/maj`, `/run` ou `/review`.

## Bonnes pratiques

- Commencer par Markdown seul si possible.
- Ajouter `cli.py` seulement si l'utilisateur a besoin d'une commande REPL réelle.
- Ajouter `runtime.py` seulement si l'utilisateur a besoin d'une action réelle.
- Ajouter `core.py` seulement si `cli.py` ou `runtime.py` ont besoin d'un backend partagé.
- Utiliser les tools existants avant de créer une nouvelle action.
- Ne jamais stocker de secret dans le skill.
- Utiliser des références comme `secret:NOM`.
- Ne pas coder de chemin absolu utilisateur.
- Ne pas dupliquer une méthode déjà portée par un tool natif.
- Garder un nom court, stable et lisible.
- Écrire pour un autre BB9 : le skill doit rester compréhensible hors de ce projet.

## Entrées Python Optionnelles

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

Un skill peut ajouter une action avec `runtime.py` en exposant `action_from_text`, `review` et `execute`.

`core.py` est réservé au backend partagé importé par `cli.py` ou `runtime.py`.

## Actions

Un skill peut proposer des actions, mais elles doivent rester contrôlées :

- préférer les briques existantes et le protocole `BB9_ACTION <skill-ou-tool> ...` ;
- demander validation humaine pour toute écriture durable ;
- ne jamais contourner guardian, gateway ou hooks ;
- documenter le protocole et les risques dans `SKILL.md`.

Si une action devient générique et stable pour tous les utilisateurs, envisager un tool natif. Si elle reste personnelle ou partageable entre utilisateurs, elle peut rester un skill.

## Protocole

```text
BB9_ACTION create_skill draft <nom>
BB9_ACTION create_skill draft <nom> local
BB9_ACTION create_skill draft <nom> global
BB9_ACTION create_skill draft <nom> cli
BB9_ACTION create_skill draft <nom> runtime
BB9_ACTION create_skill draft <nom> core
```

## Sortie

`draft <nom>` crée un squelette `SKILL.md`.

Par défaut, le skill est global et créé dans `~/.bb9/skills/`.

`draft <nom> local` crée un skill local dans `.bb9/skills/`.

`draft <nom> global` crée explicitement un skill global dans `~/.bb9/skills/`.

`draft <nom> cli` crée aussi un squelette `cli.py`.

`draft <nom> runtime` crée aussi un squelette `runtime.py`.

`draft <nom> core` crée aussi un squelette `core.py`.

## Permissions

Créer un skill écrit dans `~/.bb9/skills/` ou `.bb9/skills/`. Cette action demande toujours confirmation.

Le tool ne remplace pas la validation humaine : il prépare une base lisible que l'utilisateur peut relire et adapter.
