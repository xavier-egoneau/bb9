# Browser

## Résumé

Tester une page HTTP/HTTPS réelle avec Playwright : texte visible, sélecteurs,
interactions simples et screenshots.

## Quand l'utiliser

- L'agent crée ou modifie une page web et doit vérifier le rendu réel.
- Une page dépend de JavaScript.
- Un objectif `/goal` demande une preuve visuelle ou interactive.

## Protocole

```text
BB9_ACTION browser check url=http://127.0.0.1:3000 text="Accueil" selector=button screenshot=true
BB9_ACTION browser open url=http://127.0.0.1:3000
BB9_ACTION browser screenshot
```

## Entrées

- `check`
  - `url` : page HTTP/HTTPS à ouvrir.
  - `text` : texte visible attendu, optionnel.
  - `selector` : sélecteur CSS attendu, optionnel.
  - `screenshot` : `true` pour produire une image.
  - `viewport` : `1280x720`, optionnel.
- `open`, `extract`, `screenshot`, `click`, `type`, `close` : actions
  basiques de navigateur.

## Effets

Lance Chromium headless via Playwright si disponible. Les screenshots sont
enregistrés dans `.bb9/artifacts/screenshots/` du workspace.

## Permission

`ask` en profil `safe`, `allow` en `limited` ou `power` pour les URLs HTTP/HTTPS.
Les interactions `click` et `type` demandent validation.

## Règles

- Ne pas utiliser `browser` pour lire une page statique si `web fetch` suffit.
- Pour tester une UI créée par l'agent, préférer `browser check`.
- Retourner les preuves : URL finale, checks passés/échoués, screenshot si demandé.
- Si Playwright ou Chromium manque, retourner une observation claire.
