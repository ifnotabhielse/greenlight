from greenlight.traffic import LogShifter, KServeShifter, make_shifter
import greenlight.traffic as traffic


class _Log:
    def __init__(self): self.msgs = []
    def info(self, m): self.msgs.append(m)
    def error(self, m): self.msgs.append(m)


def test_log_shifter_is_noop():
    log = _Log()
    LogShifter().shift("default", "svc", 25, log)
    assert any("25%" in m for m in log.msgs)


def test_make_shifter_simulate(monkeypatch):
    monkeypatch.setattr(traffic, "SIMULATE", True)
    assert isinstance(make_shifter(), LogShifter)


def test_kserve_patch_payload(monkeypatch):
    captured = {}

    class FakeApi:
        def patch_namespaced_custom_object(self, **kw):
            captured.update(kw)

    s = KServeShifter.__new__(KServeShifter)  # skip kube config in __init__
    s._api = FakeApi()
    s.shift("prod", "sentiment", 25, _Log())

    assert captured["namespace"] == "prod"
    assert captured["name"] == "sentiment"
    assert captured["plural"] == "inferenceservices"
    assert captured["body"]["spec"]["predictor"]["canaryTrafficPercent"] == 25


def test_kserve_weight_clamped(monkeypatch):
    captured = {}

    class FakeApi:
        def patch_namespaced_custom_object(self, **kw):
            captured.update(kw)

    s = KServeShifter.__new__(KServeShifter)
    s._api = FakeApi()
    s.shift("default", "svc", 250, _Log())  # out of range
    assert captured["body"]["spec"]["predictor"]["canaryTrafficPercent"] == 100
