# Tools Index

- `browser` : Tester une page HTTP/HTTPS réelle avec Playwright : texte visible, sélecteurs, interactions simples et screenshots.
  Usage: L'agent crée ou modifie une page web et doit vérifier le rendu réel. Une page dépend de JavaScript. Un objectif `/goal` demande une preuve visuelle ou interactive.
  Protocole: BB9_ACTION browser check url=http://127.0.0.1:3000 text="Accueil" selector=button screenshot=true BB9_ACTION browser open url=http://127.0.0.1:3000 BB9_ACTION browser screenshot
- `caldav` : Lire et diagnostiquer un agenda CalDAV local via `vdirsyncer` et `khal`.
  Usage: L'utilisateur parle d'agenda, calendrier, rendez-vous ou disponibilité. L'utilisateur demande un briefing du jour. L'utilisateur mentionne CalDAV, iCloud, `khal` ou `vdirsyncer`. Le setup calendrier semble incomplet.
  Protocole: BB9_ACTION caldav doctor BB9_ACTION caldav agenda days=7 BB9_ACTION caldav agenda days=2 sync=false BB9_ACTION caldav maintenance refresh
- `create_skill` : Aider l'agent à concevoir et créer des skills utilisateur BB9 portables.
  Usage: L'utilisateur veut créer un nouveau skill. L'utilisateur veut transformer une méthode de travail en extension réutilisable. L'agent veut ajouter une commande REPL utilisateur. L'agent veut documenter comment utiliser des tools existants da...
  Protocole: BB9_ACTION create_skill draft <nom> BB9_ACTION create_skill draft <nom> cli
- `project-explorer` : Explorer un projet local avec des commandes de lecture et produire une synthèse courte.
  Usage: Un projet vient d'être ouvert. Le contexte Markdown est absent ou incomplet. Le système doit comprendre une structure existante avant d'agir.
- `project-onboarding` : Vérifier ou installer un contexte de gouvernance minimal dans un projet.
  Usage: Le système arrive dans un projet inconnu. Les fichiers de gouvernance sont absents. L'utilisateur demande de structurer un projet.
- `secret` : Créer et lister des références de secrets locaux sans exposer les valeurs.
  Usage: L'utilisateur veut ajouter une API key, un token ou un secret local. Une config a besoin d'une référence de secret. Un provider ou un tool échoue car un secret manque.
  Protocole: BB9_ACTION secret add <NOM_DE_VARIABLE> BB9_ACTION secret list
- `shell` : Exécuter une commande shell bornée dans le workspace courant.
- `ui_web` : Ouvrir une petite interface locale BB9 pour coller ou déposer des screenshots et obtenir des références `[image: ...]` utilisables dans la discussion.
  Usage: L'utilisateur veut montrer une image ou un screenshot à BB9. Une vérification visuelle doit être jointe à un message.
  Protocole: BB9_ACTION ui_web start port=8769 En REPL : /web
- `web` : Lire une page web ou chercher des sources publiques sans sortir du protocole BB9.
  Usage: L'utilisateur demande une information actuelle ou une source externe. L'agent doit citer ou vérifier une page HTTP/HTTPS. `shell` ne doit pas être utilisé pour faire du scraping web.
  Protocole: BB9_ACTION web fetch url=https://example.org BB9_ACTION web search query="requete utile"
