import json
from pathlib import Path
from typing import List

import numpy as np
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parent

BGE_SMALL_WEIGHTS = ROOT / "bge_small_weights.npz"
BGE_SMALL_VOCAB   = ROOT / "bge_small_vocab.json"

BGE_BASE_WEIGHTS  = ROOT / "bge_base_weights.npz"
BGE_BASE_VOCAB    = ROOT / "bge_base_vocab.json"


# ============================================================
# TOKENIZER
# ============================================================

class SimpleTokenizer:
    def __init__(self, vocab_path: Path):
        with open(vocab_path, "r", encoding="utf-8") as f:
            self.vocab = json.load(f)
        self.unk_id = self.vocab.get("[UNK]", 0)

    def encode(self, text: str) -> List[int]:
        tokens = text.strip().split()
        return [self.vocab.get(t, self.unk_id) for t in tokens]


# ============================================================
# HYBRID EMBEDDER (LOADS EXPORTED NPZ)
# ============================================================

class HybridEmbedder:
    def __init__(self, weights_path: Path, vocab_path: Path):
        self.weights = np.load(weights_path, allow_pickle=True)
        self.tokenizer = SimpleTokenizer(vocab_path)

        # Core embeddings
        self.token_emb = self.weights["token_emb"]          # [V, d_model]
        self.pos_emb   = self.weights["pos_emb"]            # [max_len, d_model]

        # Projection head
        self.proj_w = self.weights["proj_w"]                # [d_model, dim_out]
        self.proj_b = self.weights["proj_b"]                # [dim_out]

        self.d_model = self.token_emb.shape[1]
        self.dim_out = self.proj_w.shape[1]

    def _layernorm(self, x: np.ndarray, eps: float = 1e-5) -> np.ndarray:
        mean = x.mean(axis=-1, keepdims=True)
        var  = ((x - mean) ** 2).mean(axis=-1, keepdims=True)
        return (x - mean) / np.sqrt(var + eps)

    def embed(self, text: str) -> List[float]:
        token_ids = self.tokenizer.encode(text)
        if not token_ids:
            return [0.0] * self.dim_out

        seq_len = len(token_ids)
        tok = self.token_emb[token_ids]          # [seq_len, d_model]
        pos = self.pos_emb[:seq_len]            # [seq_len, d_model]
        x = tok + pos                           # [seq_len, d_model]

        # Simple hybrid: normalize, pool, project
        x = self._layernorm(x)
        pooled = x.mean(axis=0)                 # [d_model]

        pooled = pooled @ self.proj_w + self.proj_b  # [dim_out]

        # L2 normalize
        norm = np.linalg.norm(pooled)
        if norm > 0:
            pooled = pooled / norm

        return pooled.astype(np.float32).tolist()


# ============================================================
# LOAD MODELS
# ============================================================

print(">>> Loading BGE-small hybrid weights")
small_model = HybridEmbedder(BGE_SMALL_WEIGHTS, BGE_SMALL_VOCAB)

print(">>> Loading BGE-base hybrid weights")
base_model = HybridEmbedder(BGE_BASE_WEIGHTS, BGE_BASE_VOCAB)


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="MK1 Hybrid Dual-Model Embed Server")

class EmbedRequest(BaseModel):
    text: str

class EmbedResponse(BaseModel):
    dim: int
    embedding: List[float]


@app.post("/embed_small", response_model=EmbedResponse)
def embed_small(req: EmbedRequest):
    vec = small_model.embed(req.text)
    return EmbedResponse(dim=len(vec), embedding=vec)


@app.post("/embed_base", response_model=EmbedResponse)
def embed_base(req: EmbedRequest):
    vec = base_model.embed(req.text)
    return EmbedResponse(dim=len(vec), embedding=vec)


@app.get("/health")
def health():
    return JSONResponse({
        "ok": True,
        "service": "mk1_embed_server",
        "small_dim": int(small_model.dim_out),
        "base_dim": int(base_model.dim_out),
    })


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8084)
