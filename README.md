<h1 align="center">Greenlight</h1>

<p align="center"><b>Quality-gated progressive delivery for LLM & ML endpoints.</b><br>
<i>Promote a new model version only when live <b>answer quality</b> holds — eval scores, faithfulness, latency — and roll back automatically when it doesn't.</i></p>

<p align="center">
<a href="#"><img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
<a href="#"><img alt="status" src="https://img.shields.io/badge/status-alpha-orange"></a>
<a href="#"><img alt="python" src="https://img.shields.io/badge/python-3.11%2B-green"></a>
</p>

---

## Architecture

![Greenlight architecture](docs/architecture.svg)

Greenlight is the control plane that conducts tools you already run. It patches the
serving layer's own canary control, reads quality signals from your metrics stack,
and decides promote-or-rollback. It does not route requests or run evals itself.

---

## The problem

Shipping a new model or LLM version to production is a leap of faith. Your offline evals pass, you deploy, and a silent quality regression — a drop in faithfulness, a latency spike, a cost blowout — reaches users before you notice. Traditional progressive delivery (Argo Rollouts, Flagger) can canary a deployment, but it only understands **HTTP error rate and latency**. It has no idea whether your model's *answers* got worse.

Greenlight closes that gap. It's a Kubernetes controller that shifts traffic to a candidate model gradually, and at each step evaluates **model-quality gates** — eval scores, faithfulness, drift, latency p95, cost-per-request — against the stable baseline. All gates green, it advances. Any gate red, it rolls back. Automatically.

## Where Greenlight fits

| Tool category | Gates on | When | Greenlight difference |
|---|---|---|---|
| Eval-in-CI (Langfuse, Braintrust, Ragas) | eval scores | **before merge** (offline) | Greenlight gates on **live canary traffic**, post-deploy |
| LLM gateways (LiteLLM, Bifrost) | routing / cost | runtime | Greenlight is a **deployment controller**, not a request router |
| Service canary (Argo Rollouts, Flagger) | HTTP 5xx / latency | rollout | Greenlight gates on **model quality**, not just HTTP health |
| **Greenlight** | **eval + drift + latency + cost** | **progressive rollout** | **the empty slot: quality-gated runtime promotion** |

## How it works

```
ModelRollout (CR)  ──watched by──▶  Greenlight Controller
  stable: v1                          1. shift N% traffic → candidate
  candidate: v2                       2. sample live traffic → run gates
  steps: [5,25,50,100]                3. all green for window → advance
  gates: [quality≥0.85,               4. any red → rollback to stable
          p95<800ms]                  5. emit metrics + events each step
```

The controller **orchestrates existing tools** — it does not reimplement them. Serving is KServe, eval is Ragas/LLM-as-judge, metrics are Prometheus. Greenlight is the control plane that ties them into a quality-gated rollout.

## Quickstart (local demo, no GPU needed)

```bash
# 1. spin up a local cluster + install the CRD
make kind-up
make install

# 2. run the controller (simulate mode — no KServe/Ragas needed for the demo)
make run

# 3. in another shell: apply a rollout whose candidate is "worse"
kubectl apply -f examples/modelrollout-sample.yaml

# 4. watch Greenlight catch the regression and roll back
kubectl get modelrollout demo -w
```

In simulate mode the sample candidate fails its quality gate at the 25% step — you'll watch the phase walk `Progressing → RollingBack → RolledBack`. Flip the candidate to the "good" example to watch a full promotion instead.

## Testing

**Unit tests** — 25 tests across `tests/test_prometheus.py`, `tests/test_quality.py`, `tests/test_state.py`, `tests/test_traffic.py`:

```bash
make test          # or: python -m pytest -q
```

**Real-judge validation (local, zero cost)** — the LLM quality gate is validated end-to-end against a local [Ollama](https://ollama.com) judge, no cloud spend. A stdlib stub candidate (`tools/stub_candidate.py`) serves faithful (`MODE=good`) or deliberately-wrong (`MODE=bad`) answers, and the real gate scores them with `qwen2.5:3b`.

```bash
ollama pull qwen2.5:3b

# terminal A — stub candidate
MODE=good python3 tools/stub_candidate.py        # 127.0.0.1:8099

# terminal B — controller in real mode (judge points at Ollama)
GREENLIGHT_SIMULATE=false \
  JUDGE_API_BASE=http://localhost:11434/v1 JUDGE_API_KEY=ollama JUDGE_MODEL=qwen2.5:3b \
  kopf run -m greenlight.controller -n default

# terminal C — apply the local rollout and watch
kubectl apply -f examples/modelrollout-quality-local.yaml
kubectl get modelrollout rag-rollout-local -n default -w
```

`MODE=good` scores **0.933 ≥ 0.85** → canary advances 10→50→100 → **Promoted**. Restart the stub with `MODE=bad` (delete the rollout while the controller is running first, so its finalizer is removed) and it scores **0.0** → **RolledBack** at the first step.

> A reference-free LLM judge scores plausibility and coherence, not ground truth: it reliably fails incoherent, contradictory, or refusing answers, but a confident-but-wrong answer can slip through. Grounding the judge with reference answers is future work.

## How this differs from SLO-gated tools

Progressive-delivery tools (Argo Rollouts, Flagger) and the ML-focused [iter8](https://github.com/iter8-tools/iter8) gate promotion on **SLOs** — latency, error rate, custom metrics. Greenlight gates on **LLM answer quality**: an LLM-as-judge or Langfuse eval scores the candidate's actual responses, and the rollout only advances if faithfulness/quality holds. That's the gate the SLO-era tools don't have.

## Status

Alpha, v0.4. Built and working: the controller loop, the `ModelRollout` CRD, traffic stepping with auto-rollback, KServe traffic-shifting, the Prometheus p95 latency gate, and the **LLM quality gate** (LLM-as-judge + Langfuse providers, with cold-metric/inconclusive handling). The LLM-as-judge path is validated end-to-end against a real local judge (see [Testing](#testing)); the local demo also runs fully in simulate mode with no serving, eval, or judge infra required. Next: drift (Evidently) and cost gates.

## Roadmap

Next up: drift gate (Evidently) and cost-per-request gate — the dashed box in the diagram. Then: shadow-traffic eval, blue-green mode, champion/challenger, Argo Rollouts metric-provider adapter, web UI, prompt-version rollouts.

## License

Apache 2.0.
