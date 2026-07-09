import argparse
import logging
from logging.handlers import RotatingFileHandler
from typing import List, Optional, Union
import time
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ctransformers import AutoModelForCausalLM
import uvicorn

# ---------------------------------------------------------
# Logging (brick-wall, anti-blue-smoke)
# ---------------------------------------------------------
LOG_FILE = "gguf_embed_server.log"

handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,
    backupCount=3
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[handler, logging.StreamHandler()]
)

log = logging.getLogger("mk1-gguf-embed-server")

# ---------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------
app = FastAPI()

model = None
model_path: Optional[str] = None

# ---------------------------------------------------------
# Request/response models
# ---------------------------------------------------------
class EmbedRequest(BaseModel):
    input: Optional[Union[str, List[str]]] = None
    text: Optional[Union[str, List[str]]] = None
    texts: Optional[List[str]] = None
    model: Optional[str] = None


class EmbedResponseItem(BaseModel):
    embedding: List[float]
    index: int


class EmbedResponse(BaseModel):
    data: List[EmbedResponseItem]
    model: str
    object: str = "list"


# ---------------------------------------------------------
# Normalize input
# ---------------------------------------------------------
def normalize_input(payload: EmbedRequest) -> List[str]:
    if payload.texts is not None:
        if isinstance(payload.texts, list):
            return [str(t) for t in payload.texts]
        raise HTTPException(400, "`texts` must be a list")

    if payload.input is not None:
        if isinstance(payload.input, list):
            return [str(t) for t in payload.input]
        return [str(payload.input)]

    if payload.text is not None:
        if isinstance(payload.text, list):
            return [str(t) for t in payload.text]
        return [str(payload.text)]

    raise HTTPException(400, "No valid text input found (input/text/texts)")


# ---------------------------------------------------------
# Routes
# ---------------------------------------------------------
@app.get("/")
def root():
    return {
        "status": "MK1 GGUF Embed Server Online",
        "model": model_path,
    }


@app.post("/embed", response_model=EmbedResponse)
async def embed_endpoint(payload: EmbedRequest):
    global model, model_path

    if model is None:
        log.error("Embed requested but model is not loaded.")
        raise HTTPException(500, "Model not loaded.")

    start = time.time()

    try:
        texts = normalize_input(payload)
        log.info(f"Embedding request: {len(texts)} text(s)")

        # ctransformers exposes .embed for embedding-capable models
        embeddings = model.embed(texts)

        data_items: List[EmbedResponseItem] = []
        for idx, emb in enumerate(embeddings):
            data_items.append(
                EmbedResponseItem(
                    embedding=list(map(float, emb)),
                    index=idx
                )
            )

        elapsed = (time.time() - start) * 1000
        log.info(f"Embedding completed in {elapsed:.2f} ms")

        return EmbedResponse(
            data=data_items,
            model=os.path.basename(model_path) if model_path else "unknown-gguf",
            object="list"
        )

    except HTTPException:
        raise
    except Exception as e:
        log.exception("Fatal embedding error")
        raise HTTPException(500, f"Embedding error: {str(e)}")


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------
def main():
    global model, model_path

    parser = argparse.ArgumentParser(description="MK1 Pure-Python GGUF Embed Server")
    parser.add_argument("--model", type=str, required=True, help="Path to GGUF embedding model")
    parser.add_argument("--port", type=int, required=True, help="Port to listen on")
    args = parser.parse_args()

    model_path = args.model

    if not os.path.isfile(model_path):
        log.error(f"Model file not found: {model_path}")
        raise SystemExit(f"Model file not found: {model_path}")

    log.info("--------------------------------------------------")
    log.info("MK1 Pure-Python GGUF Embed Server Starting")
    log.info(f"Loading GGUF model: {model_path}")

    try:
        # model_type depends on the family; for BGE/GTE/Nomic use llama-like loader
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            model_type="llama",
            gpu_layers=0,
            config={
                "embedding": True
            }
        )
    except Exception as e:
        log.exception("Model failed to load")
        raise SystemExit(f"Model load failed: {e}")

    log.info("Model loaded successfully")
    log.info(f"Listening on port {args.port}")
    log.info("Ready for /embed requests")
    log.info("--------------------------------------------------")

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
