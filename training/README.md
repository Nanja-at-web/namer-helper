# C1 Fine-Tuning Infrastructure

This folder is a dry-run scaffold for a later external fine-tune.  It does not
train on the Namer LXC.  The intended flow is:

1. Generate confirmed training data on the Namer helper host:

   ```bash
   namer-helper generate-training \
     --rules /etc/namer-helper/rules.yaml \
     --output /etc/namer-helper/training/rules.jsonl
   ```

2. Copy the JSONL file to a CUDA host such as RunPod.

3. Run `training/train.sh` there with a base model and output directory.

4. Convert/export the resulting model for Ollama, then create it with
   `modelfiles/scene-parser.Modelfile`.

## Data Format

Each JSONL row is generated from a confirmed Rule-Learning rename:

```json
{
  "instruction": "Bereinige den verrauschten Videodateinamen und gib exakt den bestätigten kanonischen Dateinamen zurück.",
  "input": "ROCKET.2015.08.20.RCT.769.Example.Title.1080p.mp4",
  "output": "ROCKET - 2015-08-20 - RCT-769 - Example Title.mp4",
  "source": "user_confirmed",
  "metadata": {
    "oshash": "71cd356abee68aaa",
    "tpdb_id": "scene-1",
    "created": "2026-05-31"
  }
}
```

Labels must stay conservative: only confirmed `output` values belong in the
training set. Do not add guessed TPDB/StashDB suggestions as labels.

## RunPod Notes

Recommended starting point:

- GPU: any small CUDA GPU with enough VRAM for Qwen 2.5 1.5B LoRA
- Base model: `Qwen/Qwen2.5-1.5B-Instruct`
- Training mode: LoRA/QLoRA via Unsloth
- Dataset size: wait until there are enough confirmed rules to matter

Install dependencies on the CUDA host:

```bash
python -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
pip install "transformers>=4.45" "trl>=0.9" "datasets>=2.20" accelerate peft bitsandbytes
```

Then run:

```bash
bash training/train.sh /path/to/rules.jsonl /workspace/scene-parser-lora
```

The script intentionally fails early if CUDA/Unsloth is missing.

