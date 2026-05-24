from dataclasses import dataclass


@dataclass
class SolverTiming:
    t_embed_ms: float
    t_faiss_ms: float
    t_lightglue_ms: float   # sum across all k tiles
    t_total_ms: float
