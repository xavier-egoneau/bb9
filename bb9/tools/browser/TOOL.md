---
name: browser
description: Tester une page HTTP/HTTPS réelle avec Playwright : texte visible, sélecteurs, interactions simples et screenshots.
---

# Browser

## Résumé

Tester une page HTTP/HTTPS réelle avec Playwright : texte visible, sélecteurs,
interactions simples et screenshots.

## Quand l'utiliser

- L'agent crée ou modifie une page web et doit vérifier le rendu réel.
- Une page dépend de JavaScript.
- Un objectif `/goal` demande une preuve visuelle ou interactive.
- **Après avoir produit un résultat visuel (UI, maquette, page web), prends un screenshot sans attendre qu'on te le demande.** Montre le résultat à l'utilisateur avec `!\[aperçu\](.bb9/artifacts/screenshots/...)` dans ta réponse.
- L'utilisateur veut voir à quoi ressemble la page actuelle.

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
La session navigateur et ses artefacts sont attachés au workspace du `RunContext`,
pas au premier dossier courant rencontré par le processus Python.

Playwright est optionnel dans BB9. Si le package Python ou Chromium n'est pas
installé dans l'environnement qui lance `bb9`, le tool retourne une observation
`Playwright missing` ou `Could not start Playwright Chromium`.

Si une URL locale répond avec une erreur de type `ERR_EMPTY_RESPONSE`, connexion
refusée ou reset, le tool retourne une observation qui indique de démarrer un
serveur de prévisualisation responsive avec `shell` puis d'utiliser l'URL
réellement retournée.

`browser` peut être appelé depuis une surface qui tourne déjà dans une boucle
asyncio : il exécute toujours Playwright dans un thread dédié pour éviter le
conflit avec l'API sync de Playwright.

## Permission

`ask` en profil `safe`, `allow` en `limited` ou `power` pour les URLs HTTP/HTTPS.
Les interactions `click` et `type` demandent validation en `safe` seulement ;
en `limited` et `power` elles sont autorisées comme toute action de risque moyen.

Une action mal formulée (op inconnu, argument positionnel qui n'est pas une URL)
n'est pas un problème de permission : la loop retourne une observation corrective
avec l'usage attendu et l'agent doit reformuler.

## Règles

- Ne pas utiliser `browser` pour lire une page statique si `web fetch` suffit.
- Pour tester une UI créée par l'agent, préférer `browser check`.
- Retourner les preuves : URL finale, checks passés/échoués, screenshot si demandé.
- Si Playwright ou Chromium manque, retourner une observation claire.
- Si le serveur local est muet, ne pas réessayer la même navigation : demander une action qui change l'état du serveur.
- En surface async, exécuter les opérations Playwright dans le thread dédié du tool.
- Ne pas réessayer `browser` dans le même tour après un manque Playwright/Chromium.
