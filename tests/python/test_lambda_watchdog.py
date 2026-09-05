import json
from datetime import UTC, datetime

import httpx
import pytest
from llmschedbench.lambda_watchdog import load_plan, read_key, watch


def test_watchdog_waits_then_retries_and_verifies_only_selected_instance(tmp_path):
    plan = {
        "instance_id": "a" * 32,
        "instance_name": "benchmark",
        "instance_type": "gpu_1x_a6000",
        "terminate_at_utc": datetime.fromtimestamp(30, UTC).isoformat(),
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    parsed = load_plan(path)
    clock = [0]
    posts = []
    events = []

    def handler(request):
        if request.method == "POST":
            posts.append((clock[0], json.loads(request.content)))
            return httpx.Response(503 if len(posts) == 1 else 200, json={"data": {}})
        instance = {
            "id": "a" * 32,
            "name": "benchmark",
            "instance_type": {"name": "gpu_1x_a6000"},
            "status": "terminated" if len(posts) >= 2 else "active",
        }
        return httpx.Response(200, json={"data": instance})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        watch(
            parsed,
            client,
            now=lambda: clock[0],
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            emit=lambda message, **kw: events.append(json.loads(message)),
        )
    assert posts == [
        (30, {"instance_ids": ["a" * 32]}),
        (40, {"instance_ids": ["a" * 32]}),
    ]
    assert events[-1]["event"] == "terminated_verified"


def test_watchdog_identity_mismatch_never_terminates():
    requests = []

    def handler(request):
        requests.append(request.method)
        return httpx.Response(200, json={"data": {"id": "b" * 32}})

    plan = {
        "instance_id": "a" * 32,
        "deadline_epoch": 0,
        "terminate_at_utc": "1970-01-01T00:00:00Z",
    }
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError, match="identity mismatch"),
    ):
        watch(plan, client)
    assert requests == ["GET"]


def test_key_permissions_and_deadline_validation(tmp_path):
    key = tmp_path / "key"
    key.write_text("test-secret")
    key.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        read_key(key)
    key.chmod(0o600)
    assert read_key(key) == "test-secret"
    plan = tmp_path / "plan"
    plan.write_text(
        json.dumps({"instance_id": "a" * 32, "terminate_at_utc": "2026-09-05T10:00:00"})
    )
    with pytest.raises(ValueError, match="timezone"):
        load_plan(plan)
