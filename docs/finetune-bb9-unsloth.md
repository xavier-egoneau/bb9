# Fine-tune BB9 avec Unsloth

## Intention

Lancer un premier fine-tune QLoRA court de Qwen3 sur les datasets BB9 locaux :

- `datasets/bb9-agentic-qwen3/` : contrats et comportements attendus ;
- `datasets/bb9-agentic-regressions/` : problèmes récurrents extraits de
  l'historique visible.

Le premier run sert à valider le pipeline, pas à produire le modèle final.

## Commandes

Créer un environnement séparé :

```bash
uv venv .venv-unsloth --python python3.11
uv pip install --python .venv-unsloth/bin/python \
  --upgrade --force-reinstall --no-cache \
  unsloth unsloth_zoo trl peft datasets accelerate bitsandbytes
```

Smoke fine-tune vise :

```bash
.venv-unsloth/bin/python scripts/finetune_bb9_unsloth.py \
  --model-name unsloth/Qwen3-14B-unsloth-bnb-4bit \
  --max-seq-length 2048 \
  --max-steps 10 \
  --batch-size 1 \
  --grad-accum 8
```

Sur RTX 4070 12GB, Qwen3 14B ne charge pas proprement pour le fine-tuning
local : le chargement 4-bit veut déporter des modules CPU/disque.

Qwen3 8B charge, mais tombe en OOM au premier backward, même en réduisant à
512 tokens avec LoRA r=4.

Profil local validé le 2026-06-13 :

```bash
PYTORCH_ALLOC_CONF=expandable_segments:True \
UNSLOTH_COMPILE_DISABLE=1 \
TORCH_COMPILE=0 \
.venv-unsloth/bin/python scripts/finetune_bb9_unsloth.py \
  --model-name unsloth/Qwen3-4B-unsloth-bnb-4bit \
  --output-dir outputs/bb9-qwen3-4b-lora-smoke-no-compile \
  --max-steps 3 \
  --max-seq-length 512 \
  --lora-r 4 \
  --lora-alpha 8
```

Résultat : 3 steps terminés, `eval_loss` ~4.463, adapter LoRA sauvegardé dans
`outputs/bb9-qwen3-4b-lora-smoke-no-compile/`.

Pour Qwen3 14B, prévoir une machine 16GB+ VRAM au strict minimum, et idéalement
plus pour garder une fenêtre de contexte utile.

## Notes

Qwen3 doit être entraîné ici en non-thinking pour l'usage agentique BB9 : la
sortie tool doit rester exactement `BB9_ACTION ...`, sans bloc `<think>`.
