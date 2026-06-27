"""Quality gates evaluated at each rollout step.

- latency gate: queries Prometheus for the candidate's p95 (built in v0.2).
- quality gate: evaluates LLM answer quality via the providers in quality.py
  (LLM-as-judge or Langfuse) — the feature that distinguishes Greenlight from
  SLO-only progressive-delivery tools.

Both fall back to simulate mode (driven by spec.simulate) so the local demo
needs no Prometheus, no eval infra, and no judge API. A None/inconclusive
result tells the controller to wait and retry rather than pass or fail.
"""
from __future__ import annotations
from dataclasses import dataclass
import os

from .prometheus import query_scalar, p95_latency_query, PrometheusError
from .quality import score as quality_score, QualityError

SIMULATE = os.getenv("GREENLIGHT_SIMULATE", "true").lower() == "true"

DEFAULT_PROM_URL = os.getenv("PROMETHEUS_URL", "http://prometheus-server.monitoring.svc")
DEFAULT_LATENCY_METRIC = os.getenv("GREENLIGHT_LATENCY_METRIC", "request_duration_seconds_bucket")
DEFAULT_VERSION_LABEL = os.getenv("GREENLIGHT_VERSION_LABEL", "version")


@dataclass
class GateContext:
    candidate_version: str
    service: str
    window: str = "1m"
    simulate: dict | None = None
    prom_url: str = DEFAULT_PROM_URL
    latency_metric: str = DEFAULT_LATENCY_METRIC
    version_label: str = DEFAULT_VERSION_LABEL


@dataclass
class GateResult:
    name: str
    passed: bool
    observed: float | None
    threshold: float
    inconclusive: bool = False
    detail: str = ""

    def __str__(self) -> str:
        if self.inconclusive:
            return f"? {self.name}: inconclusive ({self.detail})"
        arrow = "\u2713" if self.passed else "\u2717"
        return f"{arrow} {self.name}: observed={self.observed} threshold={self.threshold} {self.detail}".rstrip()


def evaluate_gate(gate: dict, ctx: GateContext) -> GateResult:
    gtype = gate["type"]
    if gtype == "quality":
        return _quality_gate(gate, ctx)
    if gtype == "latency":
        return _latency_gate(gate, ctx)
    return GateResult(gtype, False, None, float(gate.get("threshold", 0)), detail="unknown gate type")


def _quality_gate(gate: dict, ctx: GateContext) -> GateResult:
    """Min LLM quality score (0-1). PASS when observed >= threshold."""
    threshold = float(gate["threshold"])
    if SIMULATE:
        observed = float((ctx.simulate or {}).get("qualityScore", 0.95))
        return GateResult("quality", observed >= threshold, observed, threshold)

    try:
        observed = quality_score(gate, ctx.candidate_version)
    except QualityError as exc:
        return GateResult("quality", False, None, threshold,
                          inconclusive=True, detail=f"eval: {exc}")
    if observed is None:
        return GateResult("quality", False, None, threshold,
                          inconclusive=True, detail="no scored responses yet")
    return GateResult("quality", observed >= threshold, round(observed, 3), threshold)


def _latency_gate(gate: dict, ctx: GateContext) -> GateResult:
    """Max p95 latency in ms. PASS when observed <= threshold."""
    threshold = float(gate["threshold"])
    if SIMULATE:
        observed = float((ctx.simulate or {}).get("latencyP95Ms", 200.0))
        return GateResult("latency", observed <= threshold, observed, threshold)

    promql = p95_latency_query(ctx.latency_metric, ctx.version_label,
                               ctx.candidate_version, ctx.window)
    try:
        value = query_scalar(ctx.prom_url, promql)
    except PrometheusError as exc:
        return GateResult("latency", False, None, threshold,
                          inconclusive=True, detail=f"prometheus: {exc}")
    if value is None:
        return GateResult("latency", False, None, threshold,
                          inconclusive=True, detail="no samples yet")
    observed_ms = value * 1000.0
    return GateResult("latency", observed_ms <= threshold, round(observed_ms, 1), threshold)
