import json

import numpy as np

from Memory_server.mk1_embed_server import HybridEmbedder


def test_hybrid_embedder_truncates_tokens_to_positional_table(tmp_path):
    weights_path = tmp_path / "weights.npz"
    vocab_path = tmp_path / "vocab.json"

    np.savez(
        weights_path,
        token_emb=np.ones((4, 3), dtype=np.float32),
        pos_emb=np.ones((2, 3), dtype=np.float32),
        proj_w=np.ones((3, 2), dtype=np.float32),
        proj_b=np.zeros(2, dtype=np.float32),
    )
    vocab_path.write_text(json.dumps({"[UNK]": 0, "one": 1}), encoding="utf-8")

    embedder = HybridEmbedder(weights_path, vocab_path)

    vector = embedder.embed("one one one one one")

    assert len(vector) == 2
    assert all(np.isfinite(vector))