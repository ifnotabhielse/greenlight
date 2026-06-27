"""Thin Prometheus HTTP API client — just enough to evaluate latency gates.

Greenlight queries Prometheus; it does not run it. This module wraps the
/api/v1/query instant-query endpoint and returns a single scalar, or None when
the query matched no series (e.g. the candidate hasn't served enough traffic yet).
"""
from __future__ import annotations
import httpx


class PrometheusError(RuntimeError):
    """Raised when Prometheus is unreachable or returns an error status."""


def query_scalar(prom_url: str, promql: str, timeout: float = 5.0) -> float | None:
    """Run an instant query, return the first sample's value as a float.

    Returns None when the result set is empty (no matching series / no data yet).
    Raises PrometheusError on transport or API errors.
    """
    url = prom_url.rstrip("/") + "/api/v1/query"
    try:
        resp = httpx.get(url, params={"query": promql}, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PrometheusError(f"query failed: {exc}") from exc

    if payload.get("status") != "success":
        raise PrometheusError(f"prometheus error: {payload.get('error', 'unknown')}")

    result = payload.get("data", {}).get("result", [])
    if not result:
        return None  # no data — caller treats this as inconclusive

    # instant vector: take the first series' value -> [timestamp, "value"]
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError) as exc:
        raise PrometheusError(f"unexpected response shape: {exc}") from exc


def p95_latency_query(metric: str, version_label: str, version: str, window: str) -> str:
    """Build a p95 PromQL expression over `window` for a given candidate version.

    Assumes a histogram metric (…_bucket with an `le` label), the standard shape
    for request-duration histograms exported by most serving stacks.
    """
    selector = f'{version_label}="{version}"'
    return (
        f"histogram_quantile(0.95, "
        f"sum(rate({metric}{{{selector}}}[{window}])) by (le))"
    )
