# UI Web

## Résumé

Ouvrir une petite interface locale BB9 pour coller ou déposer des screenshots et
obtenir des références `[image: ...]` utilisables dans la discussion.

## Quand l'utiliser

- L'utilisateur veut montrer une image ou un screenshot à BB9.
- Une vérification visuelle doit être jointe à un message.

## Protocole

```text
BB9_ACTION ui_web start port=8769
```

En REPL :

```text
/web
```

## Effets

Démarre un serveur HTTP local. Les images uploadées sont enregistrées dans :

```text
.bb9/uploads/web/
```

## Permission

`allow` en local. Le serveur écoute uniquement sur `127.0.0.1`.

## Règles

- Accepter uniquement PNG, JPEG, WebP et GIF.
- Générer les noms de fichiers côté serveur.
- Ne pas exposer de fichiers hors `.bb9/uploads/web/`.
- La page reste un helper minimal, pas un dashboard.
- Ce helper est distinct de `bb9 web`, qui porte l'interface chat portable. Il
  peut rester minimal ou réutiliser plus tard les primitives upload/image du
  chat web, mais il ne doit pas devenir une surface produit parallèle.
