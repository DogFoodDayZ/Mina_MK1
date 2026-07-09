# mk1_api.py
import os
import time
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from agent.core import MK1Core  # <-- note: from agent.core


# ------------------------------------------------------------
# Request Models
# ------------------------------------------------------------
class ProcessRequest(BaseModel):
    input: str


# ------------------------------------------------------------
# Initialize CORE + API
# ------------------------------------------------------------
core = MK1Core()
app = FastAPI(title="MK1 Core API", version="1.0")


STATUS_CACHE_TTL = float(os.getenv("MK1_STATUS_CACHE_TTL", "1.0"))
DB_STATUS_CACHE_TTL = float(os.getenv("MK1_DB_STATUS_CACHE_TTL", "1.0"))

_status_cache = {
    "value": None,
    "expires_at": 0.0,
}

_db_status_cache = {
    "value": None,
    "expires_at": 0.0,
}


def _get_cached(cache_obj, ttl_seconds, fetch_fn, force_refresh=False):
    now = time.monotonic()

    if not force_refresh and cache_obj["value"] is not None and now < cache_obj["expires_at"]:
        return cache_obj["value"]

    value = fetch_fn()
    cache_obj["value"] = value
    cache_obj["expires_at"] = now + max(0.0, ttl_seconds)
    return value


# ------------------------------------------------------------
# CORS (THIS IS WHERE IT GOES)
# ------------------------------------------------------------
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------
# POST /process
# ------------------------------------------------------------
@app.post("/process")
def process(req: ProcessRequest):
    """
    Main processing endpoint.
    Accepts: { "input": "string" }
    Returns: { "output": { "reply": "string" } }
    """
    return core.process(req.input)


# ------------------------------------------------------------
# GET /status
# ------------------------------------------------------------
@app.get("/status")
def status(force_refresh: bool = False):
    """
    Returns CORE health block.
    All fields must be strings.
    """
    return _get_cached(
        _status_cache,
        STATUS_CACHE_TTL,
        core.get_core_status,
        force_refresh=force_refresh,
    )


# ------------------------------------------------------------
# GET /db/status
# ------------------------------------------------------------
@app.get("/db/status")
def db_status(force_refresh: bool = False):
    """
    Returns DB health block.
    All fields must be strings.
    """
    return _get_cached(
        _db_status_cache,
        DB_STATUS_CACHE_TTL,
        core.get_db_status,
        force_refresh=force_refresh,
    )


# ------------------------------------------------------------
# GET /memory/promoted
# ------------------------------------------------------------
@app.get("/memory/promoted")
def memory_promoted(limit: int = 20):
    """
    Returns the latest auto-promoted long-term memories.
    """
    return core.get_auto_promoted_memories(limit=limit)


# ------------------------------------------------------------
# MAIN ENTRYPOINT
# ------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "mk1_api:app",
        host="127.0.0.1",
        port=8000,
        reload=False
    )
