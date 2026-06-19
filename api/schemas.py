from pydantic import BaseModel

class IngestTask(BaseModel):
    fonte: str = "all"

class AnalysisTask(BaseModel):
    ev_minimo: float = 0.02
