"""Terminate one explicitly identified Lambda instance at an absolute deadline.

Requires a separately scheduled process. This module does not launch instances or
provide a hard billing cap. Use an independent backup and verify termination.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import httpx

API = "https://cloud.lambda.ai/api/v1"


def load_plan(path: Path) -> dict:
    plan = json.loads(path.read_text())
    if not re.fullmatch(r"[0-9a-f]{32}", plan["instance_id"]):
        raise ValueError("expected one exact Lambda instance ID")
    deadline = datetime.fromisoformat(plan["terminate_at_utc"])
    if deadline.utcoffset() is None:
        raise ValueError("deadline requires timezone")
    if not plan.get("instance_type") or not plan.get("instance_name"):
        raise ValueError("instance type and name are required identity guards")
    plan["deadline_epoch"] = deadline.timestamp()
    return plan


def read_key(path: Path) -> str:
    if path.stat().st_mode & 0o077:
        raise ValueError("API key file must be private (chmod 600)")
    key = path.read_text().strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("invalid API key file")
    return key


def check_identity(instance: dict, plan: dict) -> None:
    if (
        instance["id"] != plan["instance_id"]
        or instance.get("name") != plan["instance_name"]
        or instance["instance_type"]["name"] != plan["instance_type"]
    ):
        raise ValueError("instance identity mismatch; refusing termination")


def watch(
    plan: dict,
    client,
    *,
    now=time.time,
    monotonic=time.monotonic,
    sleep=time.sleep,
    emit=print,
) -> None:
    # A monotonic deadline also prevents a backwards wall-clock adjustment from
    # extending this process's wait. Restarting uses the original UTC deadline.
    end = monotonic() + max(0, plan["deadline_epoch"] - now())
    instance_id = plan["instance_id"]
    response = client.get(API + "/instances/" + instance_id)
    response.raise_for_status()
    instance = response.json()["data"]
    check_identity(instance, plan)
    emit(
        json.dumps(
            {
                "event": "armed",
                "instance_id": instance_id,
                "terminate_at_utc": plan["terminate_at_utc"],
            }
        ),
        flush=True,
    )
    if instance["status"] == "terminated":
        emit('{"event":"terminated_verified"}', flush=True)
        return
    while now() < plan["deadline_epoch"] and monotonic() < end:
        sleep(
            min(10, max(0, plan["deadline_epoch"] - now()), max(0, end - monotonic()))
        )
    last_request = float("-inf")
    while True:
        try:
            response = client.get(API + "/instances/" + instance_id)
            response.raise_for_status()
            instance = response.json()["data"]
            check_identity(instance, plan)
            if instance["status"] == "terminated":
                emit('{"event":"terminated_verified"}', flush=True)
                return
            if instance["status"] != "terminating" or monotonic() - last_request >= 60:
                # Only this prevalidated ID; never all account instances.
                response = client.post(
                    API + "/instance-operations/terminate",
                    json={"instance_ids": [instance_id]},
                )
                response.raise_for_status()
                last_request = monotonic()
                emit('{"event":"termination_requested"}', flush=True)
        except (httpx.HTTPError, KeyError, ValueError) as error:
            # Do not log request headers or API response bodies containing secrets.
            emit(
                json.dumps(
                    {
                        "event": "termination_not_verified",
                        "error_type": type(error).__name__,
                    }
                ),
                flush=True,
            )
        sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    plan = load_plan(args.plan)
    key = read_key(args.key_file)
    with httpx.Client(
        headers={"Authorization": "Bearer " + key},
        timeout=15,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        if args.preflight_only:
            response = client.get(API + "/instances/" + plan["instance_id"])
            response.raise_for_status()
            check_identity(response.json()["data"], plan)
            print("Authenticated and verified target; no termination requested.")
        else:
            watch(plan, client)


if __name__ == "__main__":
    main()
