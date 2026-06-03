# Vision

## Résumé

Décrire une image via Ollama local (gemma4) quand le modèle principal n'a pas
la vision. Retourne une description textuelle détaillée et précise à l'agent,
qui l'intègre dans sa réponse à l'utilisateur. Ne court-circuite jamais
l'agent : l'outil est appelé par l'agent, le résultat est une observation
technique que l'agent synthétise.

## Quand l'utiliser

- Le modèle principal répond qu'il ne peut pas lire une image (« cannot read »,
  « does not support image input », « je ne peux pas voir »).
- L'utilisateur a joint une image ([image: ...] dans le message) et le modèle
  n'a pas donné de description visuelle dans sa réponse.
- L'agent a produit un screenshot avec `browser` et veut le faire décrire pour
  compléter son analyse.
- L'utilisateur demande explicitement « décris cette image ».

## Protocole

```text
BB9_ACTION vision describe path=.bb9/artifacts/screenshots/capture.png
BB9_ACTION vision describe path=.bb9/uploads/image.jpg prompt="Décris les éléments UI visibles"
```

## Entrées

- `describe`
  - `path` : chemin relatif (workspace) ou absolu vers l'image.
  - `prompt` : question optionnelle sur l'image. Par défaut : description
    détaillée du contenu, des éléments visuels, du texte présent, de la
    disposition, des couleurs et du style.

## Effets

Appelle Ollama local (`http://localhost:11434`) avec le modèle configuré
(`gemma4` par défaut). L'image est encodée en base64 et envoyée à l'API
Ollama `/api/chat`. L'observation contient la description textuelle retournée.

## Permission

`allow` dans tous les profils. Lecture seule, pas d'effet de bord sur le
workspace.

## Configuration

Le modèle vision utilisé est configuré dans `~/.bb9/settings.json` (section `vision`).
Valeur par défaut : `gemma4:latest`.

```json
{
  "vision": {
    "model": "gemma4:latest",
    "url": "http://localhost:11434",
    "timeout": 120,
    "num_predict": 512
  }
}
```

**Prérequis** : Ollama doit tourner localement et le modèle configuré doit être
installé. Le choix courant du projet est `gemma4:latest` ou `gemma4:e4b`.

Gemma4 peut être lent sur une machine locale, surtout au premier appel ou sur une
grosse image. Le runtime demande explicitement `think:false` à Ollama pour éviter
que la génération consomme le budget en raisonnement interne sans produire de
description finale.

Si aucun modèle vision n'est disponible, le tool retourne une observation
d'erreur claire.

## Règles

- Appeler `vision describe` dès que le modèle principal ne peut pas traiter
  une image, sans demander à l'utilisateur s'il veut une description.
- Utiliser un `prompt` précis quand l'utilisateur a une question spécifique
  sur l'image.
- Ne pas appeler `vision` si le modèle principal a déjà décrit l'image.
- Toujours reformuler la description technique de l'observation en langage
  naturel dans la réponse à l'utilisateur.
