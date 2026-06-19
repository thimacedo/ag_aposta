from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import risk_agent
import fetcher_agent
import db
import subprocess
import sys

app = FastAPI(title="Futebol Quant-Agent API")

class AnalyzeRequest(BaseModel):
    ev_minimo: float = 0.02

class IngestRequest(BaseModel):
    fonte: str = "all"

@app.on_event("startup")
def startup():
    db.init_db()

@app.post("/analyze")
def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(
        subprocess.run, [sys.executable, "worker_analyze.py", str(req.ev_minimo)]
    )
    return {"status": "iniciado", "ev_minimo": req.ev_minimo}

@app.post("/ingest")
def ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(
        subprocess.run, [sys.executable, "worker_ingest.py"]
    )
    return {"status": "iniciado", "fonte": req.fonte}

@app.get("/health")
def health():
    return {"status": "ok"}
