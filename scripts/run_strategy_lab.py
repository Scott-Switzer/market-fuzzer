from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.strategy_lab.api_lab import router as strategy_lab_router

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
DB_PATH = ROOT / "artifacts" / "strategy-lab.sqlite3"

app = FastAPI(title="Strategy Validation Lab", version="0.1.0")
app.include_router(strategy_lab_router, prefix="/api/strategy-lab", tags=["strategy-lab"])

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="strategy-lab-static")


@app.get("/strategy-lab")
def strategy_lab_landing() -> FileResponse:
    return FileResponse(STATIC / "strategy-lab.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "database": str(DB_PATH)}


if __name__ == "__main__":
    uvicorn.run("__main__:app", host="127.0.0.1", port=8001, log_level="info")
