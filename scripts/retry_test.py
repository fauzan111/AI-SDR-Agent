"""Concurrent idempotency proof.

Fires the SAME lead webhook many times in parallel (identical external_id) and
asserts the server created exactly ONE lead. This is what a single-request demo
can't show: that the dedupe holds under a race, not just on sequential retries.

    python scripts/retry_test.py --base-url http://localhost:8000 --concurrency 20
"""
from __future__ import annotations

import argparse
import json
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed


def _post(base_url: str, path: str, payload: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(base_url + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - local test target
        return json.loads(resp.read())


def _get(base_url: str, path: str, token: str) -> dict:
    req = urllib.request.Request(base_url + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - local test target
        return json.loads(resp.read())


def run(base_url: str, concurrency: int) -> None:
    tenant = _post(base_url, "/tenants/signup",
                   {"name": "retry-test", "icp_min_company_size": 0})
    token = tenant["access_token"]

    external_id = f"lead-{uuid.uuid4()}"
    payload = {"external_id": external_id, "email": "race@bigco.com", "company": "BigCo",
               "company_size": 300, "industry": "software",
               "message": "Want pricing and a demo for 50 seats."}

    def fire(_: int) -> dict:
        return _post(base_url, "/webhooks/leads", payload, token=token)

    ids, dedup_count = set(), 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for f in as_completed([pool.submit(fire, i) for i in range(concurrency)]):
            out = f.result()
            ids.add(out["id"])
            dedup_count += 1 if out["deduplicated"] else 0

    leads = _get(base_url, "/leads", token)
    matching = [x for x in leads if x["external_id"] == external_id]

    print(f"concurrent requests: {concurrency}")
    print(f"distinct lead ids:   {len(ids)}")
    print(f"deduplicated:        {dedup_count}")
    print(f"leads in DB w/ id:   {len(matching)}")
    if len(ids) != 1 or len(matching) != 1:
        raise SystemExit("FAIL: idempotency broke — more than one lead created")
    print("PASS: exactly one lead created under concurrent retries")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()
    run(args.base_url, args.concurrency)
