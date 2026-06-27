import pytest
import greenlight.quality as q
from greenlight.gates import _quality_gate, GateContext


def _ctx(sim=None):
    return GateContext(candidate_version="v2", service="svc", simulate=sim)


def test_parse_score_extracts_number():
    assert q._parse_score("0.92") == 0.92
    assert q._parse_score("Score: 0.7 (good)") == 0.7
    assert q._parse_score("1.5") == 1.0   # clamped
    with pytest.raises(q.QualityError):
        q._parse_score("no number here")


def test_llm_judge_mean(monkeypatch):
    monkeypatch.setattr(q, "_ask_candidate", lambda ep, p: "an answer")
    monkeypatch.setattr(q, "_judge", lambda p, a, c: 0.8)
    cfg = {"provider": "llm_judge", "candidateEndpoint": "http://x",
           "evalSet": [{"prompt": "a"}, {"prompt": "b"}], "criteria": "x"}
    assert q.score(cfg, "v2") == 0.8


def test_llm_judge_inconclusive_when_candidate_silent(monkeypatch):
    monkeypatch.setattr(q, "_ask_candidate", lambda ep, p: None)
    cfg = {"provider": "llm_judge", "candidateEndpoint": "http://x",
           "evalSet": [{"prompt": "a"}], "criteria": "x"}
    assert q.score(cfg, "v2") is None


def test_langfuse_mean(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"value": 0.9}, {"value": 0.8}]}
    monkeypatch.setattr(q.httpx, "get", lambda *a, **k: R())
    cfg = {"provider": "langfuse", "scoreName": "faithfulness"}
    assert q.score(cfg, "v2") == pytest.approx(0.85)


def test_unknown_provider_raises():
    with pytest.raises(q.QualityError):
        q.score({"provider": "nope"}, "v2")


def test_quality_gate_simulate_pass():
    g = {"type": "quality", "threshold": 0.85}
    r = _quality_gate(g, _ctx({"qualityScore": 0.95}))
    assert r.passed and not r.inconclusive


def test_quality_gate_simulate_fail():
    g = {"type": "quality", "threshold": 0.85}
    r = _quality_gate(g, _ctx({"qualityScore": 0.70}))
    assert not r.passed and not r.inconclusive


def test_quality_gate_real_inconclusive(monkeypatch):
    monkeypatch.setattr("greenlight.gates.SIMULATE", False)
    monkeypatch.setattr("greenlight.gates.quality_score", lambda cfg, v: None)
    g = {"type": "quality", "threshold": 0.85, "provider": "langfuse"}
    r = _quality_gate(g, _ctx())
    assert r.inconclusive
