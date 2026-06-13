# BB9 Agentic Qwen3 Seed Dataset

Dataset SFT seed pour apprendre à un petit modèle local le protocole agentique
BB9 : appels `BB9_ACTION`, une action par tour, usage des observations, plans
`.bb9/plan.md`, délégation bornée et wording d'erreurs runtime.

## Fichiers

- `bb9_agentic_qwen3_messages.jsonl` : format JSONL `messages`.
- `bb9_agentic_qwen3_sharegpt.jsonl` : format JSONL `conversations` compatible
  avec beaucoup de recettes ShareGPT/Unsloth.

## Taille

- train : 60
- eval : 4
- total : 64

Tags couverts : `architecture, browser, budget, build, chat, compaction, context, debug, delegate, eval, files, final, guardian, hard-case, markdown, negative, no-tool, notes, ollama, plan, project, project-summary, protocol, provider, quality, read, recovery, replace, safety, secret, session, shell, task-result, tasks, tests, tools, ui, web, worker, workspace, write_many`.

## Usage type

```python
from datasets import load_dataset

dataset = load_dataset(
    "json",
    data_files="datasets/bb9-agentic-qwen3/bb9_agentic_qwen3_messages.jsonl",
)["train"]

train = dataset.filter(lambda row: row["split"] == "train")
eval_ds = dataset.filter(lambda row: row["split"] == "eval")
```

Pour un entraînement Unsloth, garde seulement le champ `messages` ou convertis
le fichier ShareGPT avec le mapping attendu par ton script. Le dataset est
volontairement court : il sert de noyau de comportement. Il doit être complété
par des traces réelles BB9 nettoyées avant un vrai fine-tune.

## Ce que le dataset cherche à apprendre

- Produire exactement une action `BB9_ACTION` quand un tool est nécessaire.
- Ne pas mélanger prose et action tool dans le même message.
- Attendre l'observation avant de demander une deuxième action.
- Faire un bilan final depuis les observations au lieu de recopier les logs.
- Ne pas confondre outils/skills/subagents avec les faits du repo.
- Produire des plans exécutables, avec `worker: default` et `max_iterations`.
- Retourner un contrat `Status/Evidence/Blockers` dans une tâche déléguée.
