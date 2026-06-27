import pytest
from greenlight import prometheus as prom
from greenlight.gates import _latency_gate, GateContext


def test_p95_query_builder():
    q = prom.p95_latency_query("request_duration_seconds_bucket", "version", "v2", "1m")
    assert "histogram_quantile(0.95" in q
    assert 'version="v2"' in q
    assert "[1m]" in q


def _ctx():
    return GateContext(candidate_version="v2", service="svc", window="1m",
                       simulate=None, prom_url="http://prom")


def test_latency_gate_pass(monkeypatch):
    # 0.2s p95 -> 200ms, under the 800ms threshold
    monkeypatch.setattr("greenlight.gates.SIMULATE", False)
    monkeypatch.setattr("greenlight.gates.query_scalar", lambda url, q: 0.2)
    r = _latency_gate({"type": "latency", "threshold": 800}, _ctx())
    assert r.passed and not r.inconclusive and r.observed == 200.0


def test_latency_gate_fail(monkeypatch):
    monkeypatch.setattr("greenlight.gates.SIMULATE", False)
    monkeypatch.setattr("greenlight.gates.query_scalar", lambda url, q: 1.2)  # 1200ms
    r = _latency_gate({"type": "latency", "threshold": 800}, _ctx())
    assert not r.passed and not r.inconclusive


def test_latency_gate_inconclusive_on_no_data(monkeypatch):
    monkeypatch.setattr("greenlight.gates.SIMULATE", False)
    monkeypatch.setattr("greenlight.gates.query_scalar", lambda url, q: None)
    r = _latency_gate({"type": "latency", "threshold": 800}, _ctx())
    assert r.inconclusive and not r.passed


def test_latency_gate_inconclusive_on_error(monkeypatch):
    def boom(url, q):
        raise prom.PrometheusError("connection refused")
    monkeypatch.setattr("greenlight.gates.SIMULATE", False)
    monkeypatch.setattr("greenlight.gates.query_scalar", boom)
    r = _latency_gate({"type": "latency", "threshold": 800}, _ctx())
    assert r.inconclusive


def test_latency_gate_spec_simulate_overrides_env(monkeypatch):
    """SIMULATE env=false but spec.simulate present -> use simulated values."""
    monkeypatch.setattr("greenlight.gates.SIMULATE", False)
    ctx = GateContext(candidate_version="v2", service="svc", window="1m",
                      simulate={"latencyP95Ms": 150.0}, prom_url="http://prom")
    r = _latency_gate({"type": "latency", "threshold": 800}, ctx)
    assert r.passed and not r.inconclusive and r.observed == 150.0
