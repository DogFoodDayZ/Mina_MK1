import json
import torch
import numpy as np
from pathlib import Path
from transformers import AutoModel, AutoTokenizer

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
    }
}

def fuse_ffn(ffn):
    # BGE uses SwiGLU FFN: W1, W2, W3
    W1 = ffn.fc1.weight.detach().cpu().numpy()
    W2 = ffn.fc2.weight.detach().cpu().numpy()
    W3 = ffn.fc3.weight.detach().cpu().numpy()

    # Hybrid fusion: keep W1, fuse W2+W3
    fused_up = np.concatenate([W1, W3], axis=0)
    fused_down = W2
    return fused_up, fused_down

def export_model(name, cfg):
    print(f"\n>>> EXPORTING BGE-{name.upper()} (HYBRID MODE)")

    model_name = cfg["name"]
    out_weights = cfg["weights"]
    out_vocab = cfg["vocab"]

    print(">>> Downloading model:", model_name)
    model = AutoModel.from_pretrained(model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    # Save vocab
    print(">>> Saving vocab:", out_vocab)
    with open(out_vocab, "w", encoding="utf-8") as f:
        json.dump(tokenizer.get_vocab(), f, ensure_ascii=False, indent=2)

    # Extract embeddings
    token_emb = model.embeddings.word_embeddings.weight.detach().cpu().numpy()
    pos_emb   = model.embeddings.position_embeddings.weight.detach().cpu().numpy()

    # Transformer layers
    attn_q = []
    attn_k = []
    attn_v = []
    attn_o = []
    ln1_w = []
    ln1_b = []
    ln2_w = []
    ln2_b = []
    ffn_up = []
    ffn_down = []

    for layer in model.encoder.layer:
        attn = layer.attention.self
        out  = layer.attention.output

        attn_q.append(attn.query.weight.detach().cpu().numpy())
        attn_k.append(attn.key.weight.detach().cpu().numpy())
        attn_v.append(attn.value.weight.detach().cpu().numpy())
        attn_o.append(out.dense.weight.detach().cpu().numpy())

        ln1_w.append(layer.attention.output.LayerNorm.weight.detach().cpu().numpy())
        ln1_b.append(layer.attention.output.LayerNorm.bias.detach().cpu().numpy())
        ln2_w.append(layer.output.LayerNorm.weight.detach().cpu().numpy())
        ln2_b.append(layer.output.LayerNorm.bias.detach().cpu().numpy())

        up, down = fuse_ffn(layer.intermediate)
        ffn_up.append(up)
        ffn_down.append(down)

    # Final projection
    proj_w = model.pooler.dense.weight.detach().cpu().numpy().T
    proj_b = model.pooler.dense.bias.detach().cpu().numpy()

    print(">>> Saving NPZ:", out_weights)
    np.savez(
        out_weights,
        token_emb=token_emb,
        pos_emb=pos_emb,
        attn_q=np.array(attn_q, dtype=object),
        attn_k=np.array(attn_k, dtype=object),
        attn_v=np.array(attn_v, dtype=object),
        attn_o=np.array(attn_o, dtype=object),
        ln1_w=np.array(ln1_w, dtype=object),
        ln1_b=np.array(ln1_b, dtype=object),
        ln2_w=np.array(ln2_w, dtype=object),
        ln2_b=np.array(ln2_b, dtype=object),
        ffn_up=np.array(ffn_up, dtype=object),
        ffn_down=np.array(ffn_down, dtype=object),
        proj_w=proj_w,
        proj_b=proj_b,
    )

    print(f">>> EXPORT COMPLETE FOR BGE-{name.upper()}")


if __name__ == "__main__":
    for name, cfg in MODELS.items():
        export_model(name, cfg)

    print("\n>>> ALL EXPORTS COMPLETE — HYBRID MODE READY")
