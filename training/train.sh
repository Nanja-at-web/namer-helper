#!/usr/bin/env bash
set -euo pipefail

DATASET="${1:-/etc/namer-helper/training/rules.jsonl}"
OUTPUT_DIR="${2:-./scene-parser-lora}"
BASE_MODEL="${BASE_MODEL:-Qwen/Qwen2.5-1.5B-Instruct}"
MAX_STEPS="${MAX_STEPS:-120}"
PER_DEVICE_BATCH="${PER_DEVICE_BATCH:-2}"
GRAD_ACCUM="${GRAD_ACCUM:-4}"

if [[ ! -f "$DATASET" ]]; then
  echo "Dataset not found: $DATASET" >&2
  exit 2
fi

python - "$DATASET" "$OUTPUT_DIR" "$BASE_MODEL" "$MAX_STEPS" "$PER_DEVICE_BATCH" "$GRAD_ACCUM" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

dataset_path = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
base_model = sys.argv[3]
max_steps = int(sys.argv[4])
per_device_batch = int(sys.argv[5])
grad_accum = int(sys.argv[6])

try:
    import torch
except ImportError as exc:
    raise SystemExit("torch is required on the CUDA training host") from exc

if not torch.cuda.is_available():
    raise SystemExit("CUDA is required for C1 fine-tuning; run this on RunPod or another GPU host")

try:
    from datasets import load_dataset
    from trl import SFTTrainer
    from transformers import TrainingArguments
    from unsloth import FastLanguageModel
except ImportError as exc:
    raise SystemExit("Missing training dependency. See training/README.md") from exc


def format_prompt(row: dict) -> str:
    return (
        "### Instruction:\n"
        f"{row['instruction']}\n\n"
        "### Input:\n"
        f"{row['input']}\n\n"
        "### Response:\n"
        f"{row['output']}"
    )


dataset = load_dataset("json", data_files=str(dataset_path), split="train")
dataset = dataset.map(lambda row: {"text": format_prompt(row)})

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=base_model,
    max_seq_length=2048,
    dtype=None,
    load_in_4bit=True,
)

model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,
    lora_dropout=0,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=2048,
    packing=False,
    args=TrainingArguments(
        per_device_train_batch_size=per_device_batch,
        gradient_accumulation_steps=grad_accum,
        warmup_steps=5,
        max_steps=max_steps,
        learning_rate=2e-4,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=5,
        output_dir=str(output_dir),
        optim="adamw_8bit",
        seed=3407,
    ),
)

trainer.train()
model.save_pretrained(str(output_dir))
tokenizer.save_pretrained(str(output_dir))
print(f"Saved LoRA adapter to {output_dir}")
PY

