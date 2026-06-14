"""Fine-tune a Qwen3 LoRA on BB9 agentic examples with Unsloth.

This is intentionally a smoke-first training script. The first local run should
prove the pipeline, memory profile and chat template before we spend time
tuning hyperparameters.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "datasets" / "bb9-agentic-qwen3" / "bb9_agentic_qwen3_messages.jsonl"
DEFAULT_REGRESSIONS = (
    ROOT / "datasets" / "bb9-agentic-regressions" / "bb9_agentic_regressions_messages.jsonl"
)


def main() -> None:
    args = parse_args()
    train_rows, eval_rows = load_training_rows(
        args.seed_dataset,
        args.regression_dataset,
        regression_multiplier=args.regression_multiplier,
    )
    print(f"dataset: train={len(train_rows)} eval={len(eval_rows)}")

    unsloth_module = importlib.import_module("unsloth")
    FastModel = unsloth_module.FastModel
    is_bfloat16_supported = unsloth_module.is_bfloat16_supported

    import torch
    from transformers import TrainingArguments
    from trl import SFTTrainer

    from datasets import Dataset

    model, tokenizer = FastModel.from_pretrained(
        model_name=args.model_name,
        max_seq_length=args.max_seq_length,
        load_in_4bit=True,
        load_in_8bit=False,
        full_finetuning=False,
    )
    model = FastModel.get_peft_model(
        model,
        r=args.lora_r,
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        lora_alpha=args.lora_alpha,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=args.seed,
    )

    train_dataset = Dataset.from_list(format_rows(train_rows, tokenizer))
    eval_dataset = Dataset.from_list(format_rows(eval_rows, tokenizer)) if eval_rows else None

    bf16 = bool(is_bfloat16_supported())
    training_args_kwargs: dict[str, Any] = {
        "output_dir": str(args.output_dir),
        "per_device_train_batch_size": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "warmup_steps": args.warmup_steps,
        "max_steps": args.max_steps,
        "learning_rate": args.learning_rate,
        "fp16": not bf16,
        "bf16": bf16,
        "logging_steps": 1,
        "optim": "adamw_8bit",
        "weight_decay": 0.01,
        "lr_scheduler_type": "linear",
        "seed": args.seed,
        "report_to": "none",
        "save_strategy": "no",
        "eval_steps": max(args.max_steps, 1) if eval_dataset is not None else None,
    }
    args_signature = inspect.signature(TrainingArguments.__init__)
    eval_key = "eval_strategy" if "eval_strategy" in args_signature.parameters else "evaluation_strategy"
    training_args_kwargs[eval_key] = "no" if eval_dataset is None else "steps"
    training_args = TrainingArguments(**training_args_kwargs)

    trainer_kwargs: dict[str, Any] = {
        "model": model,
        "args": training_args,
        "train_dataset": train_dataset,
        "eval_dataset": eval_dataset,
        "dataset_text_field": "text",
        "max_seq_length": args.max_seq_length,
        "packing": False,
    }
    signature = inspect.signature(SFTTrainer.__init__)
    if "tokenizer" in signature.parameters:
        trainer_kwargs["tokenizer"] = tokenizer
    else:
        trainer_kwargs["processing_class"] = tokenizer
    trainer = SFTTrainer(**trainer_kwargs)

    if torch.cuda.is_available():
        print(torch.cuda.get_device_name(0))
        print(f"cuda memory before train: {torch.cuda.memory_reserved(0) / 1024**3:.2f} GiB reserved")

    trainer.train()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    print(f"saved LoRA adapter: {args.output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-name", default="unsloth/Qwen3-14B-unsloth-bnb-4bit")
    parser.add_argument("--seed-dataset", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--regression-dataset", type=Path, default=DEFAULT_REGRESSIONS)
    parser.add_argument("--regression-multiplier", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "bb9-qwen3-14b-lora-smoke")
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--warmup-steps", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--seed", type=int, default=3407)
    return parser.parse_args()


def load_training_rows(
    seed_path: Path,
    regression_path: Path,
    *,
    regression_multiplier: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = load_jsonl(seed_path)
    if regression_path.is_file():
        regression_rows = load_jsonl(regression_path)
        rows.extend(regression_rows * max(1, regression_multiplier))
    train = [row for row in rows if row.get("split", "train") == "train"]
    eval_rows = [row for row in rows if row.get("split") == "eval"]
    return train, eval_rows


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def format_rows(rows: list[dict[str, Any]], tokenizer: Any) -> list[dict[str, str]]:
    return [{"text": chat_text(row["messages"], tokenizer)} for row in rows]


def chat_text(messages: list[dict[str, str]], tokenizer: Any) -> str:
    try:
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=False,
        )
    except TypeError:
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
        )
    eos = getattr(tokenizer, "eos_token", None)
    if eos and not text.endswith(eos):
        text += eos
    return text


if __name__ == "__main__":
    main()
