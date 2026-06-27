"""LLM quality evaluation — the gate that distinguishes Greenlight from
SLO-only progressive-delivery tools.

Two providers, both returning a 0-1 quality score for the candidate:

  llm_judge  — actively probe the candidate with an eval set and score each
               response with a judge LLM (LLM-as-judge). Self-contained: needs
               only the candidate endpoint and an OpenAI-compatible judge API.

  langfuse   — passively read the mean of a named score (e.g. "faithfulness")
               over the candidate's recent traces. The "orchestrate, don't
               rebuild" path — exactly parallel to the Prometheus latency gate.

Both honour SIMULATE so the local demo runs with no eval infrastructure.
Returning None signals "inconclusive" (no data / unreachable), which the
controller treats as "wait and retry", never as a silent pass.
"""
from __future__ import annotations
import os
import re
import httpx

SIMULATE = os.getenv("GREENLIGHT_SIMULATE", "true").lower() == "true"

JUDGE_API_BASE = os.getenv("JUDGE_API_BASE", "https://api.openai.com/v1")
JUDGE_API_KEY = os.getenv("JUDGE_API_KEY", "")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-4o-mini")

_SCORE_RE = re.compile(r"(\d+(?:\.\d+)?)")


class QualityError(RuntimeError):
    """Raised when a quality score cannot be computed (vs. a low score)."""


def score(cfg: dict, candidate_version: str) -> float | None:
    """Dispatch to the configured provider. Returns a 0-1 score, or None if
    the score is inconclusive (no data / provider unreachable)."""
    provider = cfg.get("provider", "llm_judge")
    if provider == "llm_judge":
        return _llm_judge(cfg, candidate_version)
    if provider == "langfuse":
        return _langfuse(cfg, candidate_version)
    raise QualityError(f"unknown quality provider: {provider}")


def _llm_judge(cfg: dict, version: str) -> float | None:
    """Probe the candidate with each eval prompt, judge each response 0-1,
    return the mean. Criteria is a plain-language rubric (e.g. 'faithfulness
    and factual correctness')."""
    endpoint = cfg.get("candidateEndpoint")
    prompts = cfg.get("evalSet", [])
    criteria = cfg.get("criteria", "helpfulness and factual correctness")
    if not endpoint or not prompts:
        raise QualityError("llm_judge requires candidateEndpoint and evalSet")

    scores: list[float] = []
    for item in prompts:
        prompt = item["prompt"] if isinstance(item, dict) else str(item)
        answer = _ask_candidate(endpoint, prompt)
        if answer is None:
            return None  # candidate not responding yet -> inconclusive
        scores.append(_judge(prompt, answer, criteria))
    return sum(scores) / len(scores) if scores else None


def _ask_candidate(endpoint: str, prompt: str) -> str | None:
    try:
        resp = httpx.post(endpoint, json={"prompt": prompt}, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    # accept a few common response shapes
    if isinstance(data, dict):
        return data.get("response") or data.get("output") or data.get("text") or str(data)
    return str(data)


def _judge(prompt: str, answer: str, criteria: str) -> float:
    rubric = (
        f"Score the response from 0.0 to 1.0 on {criteria}. "
        f"Reply with ONLY the number.\n\n"
        f"Prompt: {prompt}\nResponse: {answer}\nScore:"
    )
    try:
        resp = httpx.post(
            f"{JUDGE_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {JUDGE_API_KEY}"},
            json={"model": JUDGE_MODEL, "temperature": 0,
                  "messages": [{"role": "user", "content": rubric}]},
            timeout=30.0,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        raise QualityError(f"judge call failed: {exc}") from exc
    return _parse_score(text)


def _parse_score(text: str) -> float:
    m = _SCORE_RE.search(text or "")
    if not m:
        raise QualityError(f"could not parse score from judge: {text!r}")
    return max(0.0, min(1.0, float(m.group(1))))


def _langfuse(cfg: dict, version: str) -> float | None:
    """Read the mean of a named score over the candidate's recent traces.
    Parallels the Prometheus latency gate: read an aggregate someone else
    already computed, don't recompute it."""
    base = cfg.get("langfuseUrl", os.getenv("LANGFUSE_URL", "https://cloud.langfuse.com"))
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    score_name = cfg.get("scoreName", "faithfulness")
    try:
        resp = httpx.get(
            base.rstrip("/") + "/api/public/scores",
            params={"name": score_name, "tags": version, "limit": 100},
            auth=(pk, sk), timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise QualityError(f"langfuse query failed: {exc}") from exc

    values = [s["value"] for s in data.get("data", []) if isinstance(s.get("value"), (int, float))]
    if not values:
        return None  # no scored traces yet -> inconclusive
    return sum(values) / len(values)
