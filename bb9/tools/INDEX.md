# Tools Index

- `browser` : Tester une page HTTP/HTTPS réelle avec Playwright : texte visible, sélecteurs, interactions simples et screenshots.
  Statut: available: Playwright package installed; Chromium verified at runtime
  Usage: L'agent crée ou modifie une page web et doit vérifier le rendu réel. Une page dépend de JavaScript. Un objectif `/goal` demande une preuve visuelle ou interactive. **Après avoir produit un résultat visuel (UI, maquette, page web), prends u...
  Protocole: BB9_ACTION browser check url=http://127.0.0.1:3000 text="Accueil" selector=button screenshot=true BB9_ACTION browser open url=http://127.0.0.1:3000 BB9_ACTION browser screenshot
- `caldav` : Lire et diagnostiquer un agenda CalDAV local via vdirsyncer et khal.
  Usage: L'utilisateur parle d'agenda, calendrier, rendez-vous ou disponibilité. L'utilisateur demande un briefing du jour. L'utilisateur mentionne CalDAV, iCloud, `khal` ou `vdirsyncer`. Le setup calendrier semble incomplet.
  Protocole: BB9_ACTION caldav doctor BB9_ACTION caldav agenda days=7 BB9_ACTION caldav agenda days=2 sync=false BB9_ACTION caldav maintenance refresh
- `create_skill` : Aider l'agent à concevoir et créer des skills utilisateur BB9 portables.
  Usage: L'utilisateur veut créer un nouveau skill. L'utilisateur veut transformer une méthode de travail en extension réutilisable. L'agent veut ajouter une commande REPL utilisateur. L'agent veut documenter comment utiliser des tools existants da...
  Protocole: BB9_ACTION create_skill draft <nom> BB9_ACTION create_skill draft <nom> local BB9_ACTION create_skill draft <nom> global BB9_ACTION create_skill draft <nom> cli BB9_ACTION create_skill draft <nom> runtime BB9_ACTION create_skill draft <nom...
- `delegate` : Lancer une tâche bornée dans un subagent du pool. Le parent reçoit un TaskResult synthétique.
  Usage: Le parent veut isoler une recherche, une vérification ou une génération bornée. La tâche peut être décrite comme une unité standalone avec objectif, contexte et sortie attendue. Le parent veut tester une action avec un profil de permission...
  Protocole: BB9_ACTION delegate run worker=dev id=T1 goal="Analyser" context="Contexte suffisant" expected="Résumé avec preuves" profile=safe BB9_ACTION delegate run worker=research id=T2 title="Lire docs" goal="Identifier les risques" context="Projet...
- `files` : Lire et modifier des fichiers du workspace par opérations bornées.
  Usage: L'utilisateur demande d'appliquer une modification dans un fichier. L'agent a déjà identifié le changement à faire. Une modification simple peut être exprimée par remplacement ou insertion.
  Protocole: BB9_ACTION files read path=src/app.js BB9_ACTION files read path=src/app.js offset=100 limit=50 BB9_ACTION files replace path=index.html old="texte actuel" new="texte remplaçant" BB9_ACTION files insert_before path=index.html marker="</hea...
- `notes` : Gérer les notes Markdown et la liste de tâches de l'agent dans son dossier.
  Usage: L'utilisateur demande de noter, retenir, garder une trace de quelque chose. L'utilisateur parle de tâches, todo, choses à faire, rappel. L'agent veut suivre une liste de sous-tâches au fil d'une conversation. L'utilisateur demande de relir...
  Protocole: BB9_ACTION notes list BB9_ACTION notes read idees-projet BB9_ACTION notes write idees-projet text="""# Idées explorer la piste A comparer B et C """ title="Idées projet" BB9_ACTION notes delete idees-projet BB9_ACTION notes todo-add Prépar...
- `project-explorer` : Explorer un projet local avec des commandes de lecture et produire une synthèse courte.
  Usage: Un projet vient d'être ouvert. Le contexte Markdown est absent ou incomplet. Le système doit comprendre une structure existante avant d'agir.
  Commandes: `/explore` : explorer le workspace courant et produire une synthèse courte.
- `project-onboarding` : Vérifier ou installer un contexte de gouvernance minimal dans un projet.
  Usage: Le système arrive dans un projet inconnu. Les fichiers de gouvernance sont absents. L'utilisateur demande de structurer un projet.
- `secret` : Créer et lister des références de secrets locaux sans exposer les valeurs.
  Usage: L'utilisateur veut ajouter une API key, un token ou un secret local. Une config a besoin d'une référence de secret. Un provider ou un tool échoue car un secret manque.
  Protocole: BB9_ACTION secret add <NOM_DE_VARIABLE> BB9_ACTION secret list
  Commandes: /secret list /secret add <NOM_DE_VARIABLE> /secrets
- `shell` : Exécuter une commande shell bornée dans le workspace courant.
- `tasks` : Persister des tâches métier simples que BB9 doit tenir dans le temps.
  Usage: L'utilisateur veut que BB9 garde une tâche à faire plus tard. Une routine, un cron ou un dream produit une suite concrète à traiter. Une tâche doit survivre à la session courante. Il faut suivre un statut métier simple : backlog, queued, r...
  Protocole: BB9_ACTION tasks create title="Relancer le dossier" prompt="Contexte utile" BB9_ACTION tasks create "Relancer le dossier" priority=high agent=default scheduled_for=2026-06-01T09:00:00+02:00 BB9_ACTION tasks list BB9_ACTION tasks list statu...
- `ui_web` : Ouvrir une interface locale BB9 pour coller ou déposer des screenshots et obtenir des références utilisables.
  Usage: L'utilisateur veut montrer une image ou un screenshot à BB9. Une vérification visuelle doit être jointe à un message.
  Protocole: BB9_ACTION ui_web start port=8769 En REPL : /web
- `vision` : Décrire une image via Ollama local quand le modèle principal n'a pas la vision.
  Usage: Le modèle principal répond qu'il ne peut pas lire une image (« cannot read », « does not support image input », « je ne peux pas voir »). L'utilisateur a joint une image ([image: ...] dans le message) et le modèle n'a pas donné de descript...
  Protocole: BB9_ACTION vision describe path=.bb9/artifacts/screenshots/capture.png BB9_ACTION vision describe path=.bb9/uploads/image.jpg prompt="Décris les éléments UI visibles"
- `web` : Lire une page web ou chercher des sources publiques sans sortir du protocole BB9.
  Usage: L'utilisateur demande une information actuelle ou une source externe. L'agent doit citer ou vérifier une page HTTP/HTTPS. `shell` ne doit pas être utilisé pour faire du scraping web.
  Protocole: BB9_ACTION web fetch url=https://example.org BB9_ACTION web search query="requete utile"
