---
activation: on-demand, /veille, veille, veille IA, veille RSS, veille sécurité, veille dev, veille frontend, suivi actualité, news IA, news dev
---

# Veille RSS curatée

## Résumé

Produire une veille d'actualité curatée en français à partir de flux RSS/Atom publics, via `web fetch`.

## Commandes

- `/veille <sujet>` : lancer une veille curatée sur un sujet (IA, dev, sécurité, frontend…)

## Activation

Quand l'utilisateur demande `/veille`, une veille, un suivi d'actualité, ou des news sur un sujet (IA, dev, sécurité, frontend, etc.).

## Protocole

### 1. Sélectionner les sources pertinentes

En fonction du sujet demandé, choisir 4 à 8 flux dans la liste ci-dessous. Préférer les sources à `reliability` élevée et `noise` faible.

### 2. Collecter les flux

Pour chaque source retenue :

```text
BB9_ACTION web fetch url=<url-du-flux> max_chars=8000
```

Lancer les fetches en séquence. Si un flux échoue (HTTP error, timeout), passer au suivant sans bloquer.

Le contenu retourné est du XML RSS ou Atom brut. En extraire :
- `<title>` de chaque `<item>` ou `<entry>`
- `<link>` (ou attribut `href` pour Atom)
- `<pubDate>` / `<published>` / `<updated>`
- `<description>` / `<summary>` / `<content>`

Garder les 10 à 15 articles les plus récents par flux.

### 3. Curation éditoriale

Après collecte, appliquer une vraie curation — pas un simple filtre de score :

- Retirer les hors-sujet et faux positifs ;
- Rétrograder : contenus marketing, annonces sponsors, offres d'emploi, quizzes, communiqués faibles ;
- Regrouper par thème réel ;
- Résumer en français (titre + 1-2 phrases) ;
- Classer par importance pratique, pas par ordre chronologique ;
- Signaler les limites si peu de sources ont répondu.

### 4. Format de sortie

```md
# Veille <Sujet> — synthèse curatée

## À lire en priorité

### [Titre de l'article](https://url)
**Source :** Nom du flux  
**Type :** modèle / recherche / infra / produit / business / régulation / sécurité / dev tooling  
**Pourquoi c'est important :** ...

## À surveiller

### [Titre](https://url)
**Source :** ...  
**Résumé :** ...

## Bruit / faible priorité
- [Titre](https://url) — raison courte.

## Tendances observées
1. ...
2. ...
```

Toujours utiliser des liens Markdown cliquables sur le titre. Ne jamais afficher une URL brute seule.

## Sources disponibles

### IA / Machine Learning

| id | Nom | URL | reliability | noise |
|----|-----|-----|------------|-------|
| hugging-face-blog | Hugging Face Blog | https://huggingface.co/blog/feed.xml | 5 | 2 |
| ollama-blog | Ollama Blog | https://ollama.com/blog/rss.xml | 5 | 1 |
| openai-news | OpenAI News | https://openai.com/news/rss.xml | 5 | 2 |
| anthropic-news | Anthropic News | https://www.anthropic.com/rss.xml | 5 | 1 |
| google-ai-blog | Google AI Blog | https://blog.google/technology/ai/rss/ | 5 | 2 |
| mit-tech-review-ai | MIT Technology Review – AI | https://www.technologyreview.com/feed/ | 5 | 2 |
| ollama-releases | Ollama Releases | https://github.com/ollama/ollama/releases.atom | 5 | 1 |

### Dev / Open Source

| id | Nom | URL | reliability | noise |
|----|-----|-----|------------|-------|
| github-blog | GitHub Blog | https://github.blog/feed/ | 5 | 2 |
| stackoverflow-blog | Stack Overflow Blog | https://stackoverflow.blog/feed/ | 4 | 2 |
| hacker-news-frontpage | Hacker News Frontpage | https://hnrss.org/frontpage | 4 | 3 |
| vscode-releases | VS Code Releases | https://github.com/microsoft/vscode/releases.atom | 5 | 1 |

### Frontend / Web

| id | Nom | URL | reliability | noise |
|----|-----|-----|------------|-------|
| vercel-blog | Vercel Blog | https://vercel.com/blog/rss.xml | 5 | 2 |
| smashing-magazine | Smashing Magazine | https://www.smashingmagazine.com/feed/ | 5 | 2 |
| dev-to-feed | DEV Community | https://dev.to/feed | 3 | 4 |
| react-releases | React Releases | https://github.com/facebook/react/releases.atom | 5 | 1 |
| vite-releases | Vite Releases | https://github.com/vitejs/vite/releases.atom | 5 | 1 |

### Cybersécurité

| id | Nom | URL | reliability | noise |
|----|-----|-----|------------|-------|
| krebs-on-security | Krebs on Security | https://krebsonsecurity.com/feed/ | 5 | 2 |
| the-hacker-news | The Hacker News | https://feeds.feedburner.com/TheHackersNews | 4 | 3 |
| bleeping-computer | BleepingComputer | https://www.bleepingcomputer.com/feed/ | 4 | 3 |
| schneier-security | Schneier on Security | https://www.schneier.com/feed/atom | 5 | 1 |

### Tech généraliste

| id | Nom | URL | reliability | noise |
|----|-----|-----|------------|-------|
| bbc-technology | BBC Technology | https://feeds.bbci.co.uk/news/technology/rss.xml | 4 | 3 |
| ars-technica | Ars Technica | https://feeds.arstechnica.com/arstechnica/index | 5 | 2 |
| wired | Wired | https://www.wired.com/feed/rss | 4 | 3 |
| the-register | The Register | https://www.theregister.com/headlines.atom | 4 | 3 |

### Actualité généraliste

| id | Nom | URL | reliability | noise |
|----|-----|-----|------------|-------|
| reuters-world | Reuters – World | https://feeds.reuters.com/reuters/worldNews | 5 | 2 |
| reuters-tech | Reuters – Technology | https://feeds.reuters.com/reuters/technologyNews | 5 | 2 |
| le-monde | Le Monde | https://www.lemonde.fr/rss/une.xml | 5 | 2 |
| france-info | France Info | https://www.francetvinfo.fr/titres.rss | 4 | 3 |
| the-guardian-world | The Guardian – World | https://www.theguardian.com/world/rss | 5 | 2 |
| le-figaro | Le Figaro | https://www.lefigaro.fr/rss/figaro_actualites.xml | 4 | 3 |
| bbc-world | BBC News – World | https://feeds.bbci.co.uk/news/world/rss.xml | 5 | 2 |

### Science

| id | Nom | URL | reliability | noise |
|----|-----|-----|------------|-------|
| nature-news | Nature News | https://www.nature.com/nature.rss | 5 | 1 |
| new-scientist | New Scientist | https://www.newscientist.com/feed/home/ | 4 | 2 |

## Règles

- Ne jamais afficher d'URL brute sans lien Markdown.
- Si un flux retourne une erreur, noter la source manquante à la fin.
- Ne pas inventer d'articles : tout article cité doit venir d'un flux collecté.
- Pour une veille IA, privilégier : modèles, releases, benchmarks, agents, outils dev, IA locale, recherche appliquée, infra, régulation.
- Pour une veille IA, traiter avec prudence : cas clients marketing, hackathons/vouchers, offres d'emploi, articles politiques peu actionnables.
- `web fetch` peut retourner du XML tronqué si le flux est long — s'arrêter aux items bien formés.
