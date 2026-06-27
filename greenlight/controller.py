"""Greenlight controller: watches ModelRollout resources and drives the
quality-gated progressive rollout loop.

The reconcile model: each tick, look at where the rollout is, evaluate the gates
for the current step, then advance, promote, hold (metrics not ready), or roll back.
Every decision is logged and recorded in .status so `kubectl get modelrollout`
tells the whole story.
"""
from __future__ import annotations
import datetime as dt

import kopf
from prometheus_client import Counter, Gauge, start_http_server

from .state import Phase, is_terminal, next_step_index
from .gates import evaluate_gate, GateContext
from .traffic import make_shifter, TrafficError

# Chosen once at import: KServe patcher in real mode, log-only in simulate mode.
_SHIFTER = make_shifter()

# Max consecutive inconclusive evaluations before we give up and roll back:
# can't verify the candidate is safe -> don't promote it.
MAX_INCONCLUSIVE = 6

# --- metrics -----------------------------------------------------------------
PROMOTIONS = Counter("greenlight_promotions_total", "Candidate promotions", ["rollout"])
ROLLBACKS = Counter("greenlight_rollbacks_total", "Candidate rollbacks", ["rollout", "gate"])
WEIGHT = Gauge("greenlight_candidate_weight", "Current candidate traffic weight", ["rollout"])


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _shift_traffic(service: str, candidate_version: str, weight: int, logger, namespace: str = "default") -> None:
    """Point `weight`% of traffic at the candidate via the serving layer's
    own canary control (KServe canaryTrafficPercent). A TrafficError is logged
    and surfaced so the reconcile loop can hold rather than silently proceed."""
    try:
        _SHIFTER.shift(namespace, service, weight, logger)
    except TrafficError as exc:
        logger.error(f"[traffic] {exc}")
        raise


def _build_context(spec) -> GateContext:
    prom = spec.get("prometheus", {}) or {}
    ctx = GateContext(
        candidate_version=spec["candidate"]["version"],
        service=spec["service"],
        window=spec.get("stepDuration", "1m"),
        simulate=spec.get("simulate"),
    )
    if prom.get("url"):
        ctx.prom_url = prom["url"]
    if prom.get("latencyMetric"):
        ctx.latency_metric = prom["latencyMetric"]
    if prom.get("versionLabel"):
        ctx.version_label = prom["versionLabel"]
    return ctx


@kopf.on.startup()
def _startup(settings: kopf.OperatorSettings, **_):
    settings.persistence.finalizer = "greenlight.dev/finalizer"
    start_http_server(9100)  # Prometheus scrape endpoint for Greenlight's own metrics


@kopf.on.create("greenlight.dev", "v1alpha1", "modelrollouts")
def on_create(spec, patch, logger, **_):
    logger.info(f"new ModelRollout: {spec['service']} "
                f"{spec['stable']['version']} -> {spec['candidate']['version']}")
    patch.status.update({
        "phase": Phase.PROGRESSING.value,
        "currentStepIndex": 0,
        "currentWeight": 0,
        "inconclusiveCount": 0,
        "message": "starting rollout",
        "lastTransitionTime": _now(),
    })


def _rollback(spec, patch, name, gate, detail, logger, namespace="default"):
    _shift_traffic(spec["service"], spec["candidate"]["version"], 0, logger, namespace)
    WEIGHT.labels(name).set(0)
    ROLLBACKS.labels(name, gate).inc()
    patch.status.update({
        "phase": Phase.ROLLED_BACK.value,
        "currentWeight": 0,
        "message": f"rolled back: {detail}",
        "lastTransitionTime": _now(),
    })


@kopf.timer("greenlight.dev", "v1alpha1", "modelrollouts", interval=5.0)
def reconcile(spec, status, patch, name, namespace, logger, **_):
    """The heart of Greenlight — one step of the rollout loop per tick."""
    phase = status.get("phase")
    if is_terminal(phase):
        return

    steps: list[int] = spec["steps"]
    idx: int = status.get("currentStepIndex", 0)
    target_weight = steps[idx]
    candidate_version = spec["candidate"]["version"]

    # 1. ensure traffic is at the current step's weight before evaluating
    if status.get("currentWeight") != target_weight:
        _shift_traffic(spec["service"], candidate_version, target_weight, logger, namespace)
        WEIGHT.labels(name).set(target_weight)
        patch.status.update({
            "currentWeight": target_weight,
            "message": f"evaluating gates at {target_weight}%",
            "lastTransitionTime": _now(),
        })
        return  # let traffic settle; next tick scores it

    # 2. evaluate every gate for this step
    ctx = _build_context(spec)
    results = [evaluate_gate(g, ctx) for g in spec["gates"]]
    for r in results:
        logger.info(f"[gate] step {idx} ({target_weight}%): {r}")

    failed = [r for r in results if not r.passed and not r.inconclusive]
    inconclusive = [r for r in results if r.inconclusive]

    # 3a. a gate genuinely failed -> roll back
    if failed:
        gate = failed[0].name
        logger.warning(f"gate '{gate}' failed at {target_weight}% — rolling back")
        _rollback(spec, patch, name, gate, f"gate '{gate}' failed ({failed[0]})", logger, namespace)
        return

    # 3b. a gate couldn't be evaluated yet (cold metrics) -> hold and retry,
    #     but give up after MAX_INCONCLUSIVE — an unverifiable gate is not a pass.
    if inconclusive:
        count = status.get("inconclusiveCount", 0) + 1
        if count >= MAX_INCONCLUSIVE:
            gate = inconclusive[0].name
            logger.warning(f"gate '{gate}' inconclusive {count}x — cannot verify, rolling back")
            _rollback(spec, patch, name, gate,
                      f"gate '{gate}' never produced data ({inconclusive[0].detail})", logger, namespace)
            return
        logger.info(f"gates inconclusive ({count}/{MAX_INCONCLUSIVE}) — waiting for metrics")
        patch.status.update({
            "inconclusiveCount": count,
            "message": f"waiting for metrics ({count}/{MAX_INCONCLUSIVE})",
            "lastTransitionTime": _now(),
        })
        return

    # 3c. all gates green -> reset retry budget, then advance or promote
    patch.status.update({"inconclusiveCount": 0})
    nxt = next_step_index(idx, steps)
    if nxt is None:
        logger.info(f"all gates green at 100% — promoting {candidate_version}")
        PROMOTIONS.labels(name).inc()
        patch.status.update({
            "phase": Phase.PROMOTED.value,
            "message": f"promoted {candidate_version}",
            "lastTransitionTime": _now(),
        })
    else:
        logger.info(f"step {idx} green — advancing to step {nxt} ({steps[nxt]}%)")
        patch.status.update({
            "currentStepIndex": nxt,
            "message": f"advancing to {steps[nxt]}%",
            "lastTransitionTime": _now(),
        })
