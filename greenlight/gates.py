"""Quality gates evaluated at each rollout step.

Each gate returns a GateResult. The quality gate still runs in simulate mode
(Ragas/LLM-as-judge wiring is a later commit); the latency gate now queries
Prometheus for real, falling back to simulate mode only when GREENLIGHT_SIMULATE
is set so the local demo keeps working without a metrics stack.
"""
from __future__ import annotations
from dataclasses import dataclass
import os

from .prometheus import query_scalar, p95_latency_query, PrometheusError

SIMULATE = os.getenv("GREENLIGHT_SIMULATE", "true").lower() == "true"

# Defaults for the latency gate's Prometheus query; override per-rollout via spec.prometheus.
DEFAULT_PROM_URL = os.getenv("PROMETHEUS_URL", "http://prometheus-server.monitoring.svc")
DEFAULT_LATENCY_METRIC = os.getenv("GREENLIGHT_LATENCY_METRIC", "request_duration_seconds_bucket")
DEFAULT_VERSION_LABEL = os.getenv("GREENLIGHT_VERSION_LABEL", "version")


@dataclass
class GateContext:
    """Everything a gate needs to evaluate itself, assembled by the controller."""
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
    threshold = float(gate["threshold"])
    if gtype == "quality":
        return _quality_gate(threshold, ctx)
    if gtype == "latency":
        return _latency_gate(threshold, ctx)
    return GateResult(gtype, False, None, threshold, detail="unknown gate type")


def _quality_gate(threshold: float, ctx: GateContext) -> GateResult:
    """Min eval score (0-1) on sampled live responses. PASS when observed >= threshold."""
    if SIMULATE:
        observed = float((ctx.simulate or {}).get("qualityScore", 0.95))
    else:
        # TODO: sample N live candidate responses, score with Ragas / LLM-as-judge.
        raise NotImplementedError("wire Ragas/Langfuse eval here")
    return GateResult("quality", observed >= threshold, observed, threshold)


def _latency_gate(threshold: float, ctx: GateContext) -> GateResult:
    """Max p95 latency in ms. PASS when observed <= threshold.

    Queries Prometheus for the candidate's p95 over the step window. Empty result
    (no traffic/samples yet) -> inconclusive, so the controller waits rather than
    rolling back on a cold metric. Prometheus errors -> inconclusive too (don't
    promote on an unverifiable gate, but don't hard-fail on a flaky scrape).
    """
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

    observed_ms = value * 1000.0  # histogram is in seconds; threshold is ms
    return GateResult("latency", observed_ms <= threshold, round(observed_ms, 1), threshold)
