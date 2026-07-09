import json
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parent

MODELS = {
    "small": {
        "name": "BAAI/bge-small-en-v1.5",
        "weights": ROOT / "bge_small_weights.npz",
        "vocab":   ROOT / "bge_small_vocab.json",
    },
    "base": {
        "name": "BAAI/bge-base-en-v1.5",
        "weights": ROOT / "bge_base_weights.npz",
        "vocab":   ROOT / "bge_base_vocab.json",
    },
}


# ============================================================
# EXPORT LOGIC (CPU ONLY)
# ============================================================

def export_model(name: str, cfg: dict):
    print(f"\n============================================================")
    print(f">>> EXPORTING {cfg['name']} AS {name.upper()}")
    print(f"============================================================")

    model_name = cfg["name"]
    out_weights = cfg["weights"]
    out_vocab = cfg["vocab"]

    # CPU-only load
    print(">>> Downloading / loading model on CPU:", model_name)
    model = AutoModel.from_pretrained(model_name, torch_dtype=torch.float32)
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    # -----------------------------
    # Vocab export
    # -----------------------------
    print(">>> Saving vocab:", out_vocab)
    with open(out_vocab, "w", encoding="utf-8") as f:
        json.dump(tokenizer.get_vocab(), f, ensure_ascii=False, indent=2)

    # -----------------------------
    # Core weights export
    # (minimal but stable: embeddings + position + projection)
    # -----------------------------
    print(">>> Extracting core weights...")

    # Token + positional embeddings
    token_emb = (
        model.embeddings.word_embeddings.weight.detach().cpu().numpy()
    )  # [vocab, d_model]
    pos_emb = (
        model.embeddings.position_embeddings.weight.detach().cpu().numpy()
    )  # [max_len, d_model]

    # Projection head (pooler dense)
    # BGE uses mean pooling + linear projection
    proj_w = model.pooler.dense.weight.detach().cpu().numpy().T  # [d_model, dim_out]
    proj_b = model.pooler.dense.bias.detach().cpu().numpy()      # [dim_out]

    d_model = token_emb.shape[1]
    dim_out = proj_w.shape[1]

    print(">>> d_model:", d_model, "dim_out:", dim_out)

    # -----------------------------
    # Save NPZ
    # -----------------------------
    print(">>> Saving NPZ:", out_weights)
    np.savez(
        out_weights,
        d_model=np.array(d_model, dtype=np.int32),
        dim_out=np.array(dim_out, dtype=np.int32),
        token_emb=token_emb.astype(np.float32),
        pos_emb=pos_emb.astype(np.float32),
        proj_w=proj_w.astype(np.float32),
        proj_b=proj_b.astype(np.float32),
    )

    print(f">>> EXPORT COMPLETE FOR {name.upper()}")
    print(">>> Weights:", out_weights)
    print(">>> Vocab:  ", out_vocab)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    for name, cfg in MODELS.items():
        export_model(name, cfg)

    print("\n============================================================")
    print(">>> ALL EXPORTS COMPLETE")
    print(">>> Files written:")
    print("    - bge_small_weights.npz")
    print("    - bge_small_vocab.json")
    print("    - bge_base_weights.npz")
    print("    - bge_base_vocab.json")
    print("============================================================")
