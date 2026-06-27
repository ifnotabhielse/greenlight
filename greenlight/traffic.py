"""Traffic shifting — the write side of the rollout loop.

Greenlight does not route requests itself. It patches the serving layer's own
canary control. For KServe that's `spec.predictor.canaryTrafficPercent` on the
InferenceService; Greenlight just sets the number and lets KServe do the routing.

A LogShifter keeps the local demo working without a serving stack: when
GREENLIGHT_SIMULATE is set (or the kube client can't load), traffic shifts are
logged instead of applied.
"""
from __future__ import annotations
import os

SIMULATE = os.getenv("GREENLIGHT_SIMULATE", "true").lower() == "true"

KSERVE_GROUP = "serving.kserve.io"
KSERVE_VERSION = "v1beta1"
KSERVE_PLURAL = "inferenceservices"


class TrafficError(RuntimeError):
    """Raised when a traffic shift cannot be applied."""


class LogShifter:
    """No-op shifter for demos/tests: records intent, changes nothing."""
    def shift(self, namespace: str, service: str, weight: int, logger) -> None:
        logger.info(f"[traffic] (simulate) {namespace}/{service}: candidate -> {weight}%")


class KServeShifter:
    """Patches an InferenceService's canaryTrafficPercent via the K8s API."""
    def __init__(self):
        from kubernetes import client, config
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self._api = client.CustomObjectsApi()

    def shift(self, namespace: str, service: str, weight: int, logger) -> None:
        weight = max(0, min(100, int(weight)))
        patch = {"spec": {"predictor": {"canaryTrafficPercent": weight}}}
        try:
            self._api.patch_namespaced_custom_object(
                group=KSERVE_GROUP, version=KSERVE_VERSION, namespace=namespace,
                plural=KSERVE_PLURAL, name=service, body=patch,
            )
        except Exception as exc:  # ApiException and transport errors
            raise TrafficError(f"failed to patch {namespace}/{service}: {exc}") from exc
        logger.info(f"[traffic] {namespace}/{service}: canaryTrafficPercent -> {weight}%")


def make_shifter():
    """Pick the shifter: log in simulate mode, otherwise patch KServe.

    Falls back to LogShifter if the kube client can't initialise, so a
    misconfigured cluster degrades to a visible no-op rather than crashing
    the controller on startup.
    """
    if SIMULATE:
        return LogShifter()
    try:
        return KServeShifter()
    except Exception:
        return LogShifter()
