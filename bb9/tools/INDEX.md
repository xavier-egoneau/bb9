# Tools Index

- `browser` : Tester une page HTTP/HTTPS réelle avec Playwright : texte visible, sélecteurs, interactions simples et screenshots.
  Statut: unavailable: Playwright Python package missing
  Action: BB9_ACTION browser check url=http://127.0.0.1:3000 text="Accueil" selector=button screenshot=true BB9_ACTION browser open url=http://127.0.0.1:3000 BB9_ACTION browser...
- `caldav` : Lire et diagnostiquer un agenda CalDAV local via vdirsyncer et khal.
  Action: BB9_ACTION caldav doctor BB9_ACTION caldav agenda days=7 BB9_ACTION caldav agenda days=2 sync=false BB9_ACTION caldav maintenance refresh
- `create_skill` : Aider l'agent à concevoir et créer des skills utilisateur BB9 portables.
  Action: BB9_ACTION create_skill draft <nom> BB9_ACTION create_skill draft <nom> local BB9_ACTION create_skill draft <nom> global BB9_ACTION create_skill draft <nom> cli BB9_AC...
- `delegate` : Lancer une tâche bornée dans un subagent du pool. Le parent reçoit un TaskResult synthétique.
  Action: BB9_ACTION delegate run worker=dev id=T1 goal="Analyser" context="Contexte suffisant" expected="Résumé avec preuves" profile=safe BB9_ACTION delegate run worker=resear...
- `files` : Lire et modifier des fichiers du workspace par opérations bornées.
  Action: BB9_ACTION files read path=src/app.js BB9_ACTION files read path=src/app.js offset=100 limit=50 BB9_ACTION files replace path=index.html old="texte actuel" new="texte...
- `local_auto_edit` : Deleguer une tache de patch/review au runtime local optimise.
  Action: BB9_ACTION local_auto_edit run prompt="Fix the bug without changing tests." file=app/main.py file=tests/test_main.py BB9_ACTION local_auto_edit run prompt="Fix the bug...
- `notes` : Gérer les notes Markdown et la liste de tâches de l'agent dans son dossier.
  Action: BB9_ACTION notes list BB9_ACTION notes read idees-projet BB9_ACTION notes write idees-projet text="""# Idées explorer la piste A comparer B et C """ title="Idées proje...
- `project-explorer` : Explorer un projet local avec des commandes de lecture et produire une synthèse courte.
  Commandes: `/explore`
- `project-onboarding` : Vérifier ou installer un contexte de gouvernance minimal dans un projet.
- `secret` : Créer et lister des références de secrets locaux sans exposer les valeurs.
  Action: BB9_ACTION secret add <NOM_DE_VARIABLE> BB9_ACTION secret list
  Commandes: /secret /secrets ...
- `shell` : Exécuter une commande shell bornée dans le workspace courant.
- `tasks` : Persister des tâches métier simples que BB9 doit tenir dans le temps.
  Action: BB9_ACTION tasks create title="Relancer le dossier" prompt="Contexte utile" BB9_ACTION tasks create "Relancer le dossier" priority=high agent=default scheduled_for=202...
- `ui_web` : Ouvrir une interface locale BB9 pour coller ou déposer des screenshots et obtenir des références utilisables.
  Action: BB9_ACTION ui_web start port=8769 En REPL : /web
- `vision` : Décrire une image via Ollama local quand le modèle principal n'a pas la vision.
  Action: BB9_ACTION vision describe path=.bb9/artifacts/screenshots/capture.png BB9_ACTION vision describe path=.bb9/uploads/image.jpg prompt="Décris les éléments UI visibles"
- `web` : Lire une page web ou chercher des sources publiques sans sortir du protocole BB9.
  Action: BB9_ACTION web fetch url=https://example.org BB9_ACTION web search query="requete utile"
