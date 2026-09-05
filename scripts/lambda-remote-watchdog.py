"""Standard-library watchdog installed by cloud-init, independent of the client Mac."""

import json
import sys
import time
import urllib.request

API = "https://cloud.lambda.ai/api/v1"


def watch(config, request, now=time.time, sleep=time.sleep):
    print("remote_watchdog_armed deadline=" + str(config["deadline_epoch"]), flush=True)
    while now() < config["deadline_epoch"]:
        sleep(min(10, config["deadline_epoch"] - now()))
    last_request = float("-inf")
    while True:
        try:
            matches = [
                i
                for i in request("GET", "/instances")["data"]
                if i.get("name") == config["instance_name"]
                and i["instance_type"]["name"] == config["instance_type"]
            ]
            if len(matches) != 1:
                print("target_count=" + str(len(matches)), flush=True)
                sleep(10)
                continue
            instance = matches[0]
            if instance["status"] != "terminating" or now() - last_request >= 60:
                request(
                    "POST",
                    "/instance-operations/terminate",
                    {"instance_ids": [instance["id"]]},
                )
                last_request = now()
                print("termination_requested", flush=True)
        except Exception as error:  # noqa: BLE001 -- retry without exposing credentials
            print("termination_retry=" + type(error).__name__, flush=True)
        sleep(10)


def main():
    with open(sys.argv[1]) as handle:
        config = json.load(handle)

    def request(method, route, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            API + route,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + config["api_key"],
                "Content-Type": "application/json",
                "User-Agent": "llmschedbench-watchdog/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    watch(config, request)


if __name__ == "__main__":
    main()
