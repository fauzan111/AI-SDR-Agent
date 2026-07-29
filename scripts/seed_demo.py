"""Seed the SDR agent with a mix of leads so you can see qualify / disqualify /
dedupe in one run.

    python scripts/seed_demo.py --base-url http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import urllib.request
import uuid

LEADS = [
    # Strong, in-ICP -> should qualify + schedule.
    {"external_id": "seed-1", "email": "vp@bigsoftware.com", "name": "VP Eng",
     "company": "BigSoftware", "company_size": 400, "industry": "software",
     "message": "Budget approved, want a demo and pricing for 60 seats this quarter."},
    # Too small -> hard-filtered out (no LLM call).
    {"external_id": "seed-2", "email": "solo@tinyshop.com", "name": "Solo", "company": "TinyShop",
     "company_size": 3, "industry": "software", "message": "just browsing"},
    # Off-ICP industry -> disqualified.
    {"external_id": "seed-3", "email": "ops@farmco.com", "name": "Ops", "company": "FarmCo",
     "company_size": 200, "industry": "agriculture", "message": "interested in pricing"},
]


def _post(base_url: str, path: str, payload: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(base_url + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - local demo target
        return json.loads(resp.read())


def main(base_url: str) -> None:
    tenant = _post(base_url, "/tenants/signup", {
        "name": f"Demo SDR {uuid.uuid4().hex[:6]}", "icp_min_company_size": 50,
        "icp_industries": "software,finance", "qualification_threshold": 60,
    })
    token = tenant["access_token"]
    print(f"workspace: {tenant['tenant_id']}\ntoken:     {token}\n")

    for lead in LEADS:
        out = _post(base_url, "/webhooks/leads", lead, token=token)
        print(f"{lead['company']:14} -> {out['status']:13} score={out['score']} "
              f"crm={out['crm_id'] or '-'}")

    print("\n--- re-sending seed-1 (idempotency) ---")
    again = _post(base_url, "/webhooks/leads", LEADS[0], token=token)
    once_more = _post(base_url, "/webhooks/leads", LEADS[0], token=token)
    print(f"deduplicated={again['deduplicated']}  same_id={again['id'] == once_more['id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    main(parser.parse_args().base_url)
