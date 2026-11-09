"""Sign and send a sample webhook, to try the endpoint or demo the pipeline.

    export WEBHOOK_SECRET=whsec_...       # printed by `python -m app.cli create-webhook`
    uv run python -m scripts.send_webhook --endpoint whk_... --provider generic
    uv run python -m scripts.send_webhook --endpoint whk_... --provider tally

The secret is read from the environment so it stays out of shell history. Send the same
--event-id twice to see deduplication.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from app.webhooks.signatures import sign_generic, sign_tally

SAMPLE_MESSAGE = (
    "Hi, I'm Dana from Brightline Dental. We need our booking form connected to HubSpot so "
    "new patients land in our CRM automatically. Budget about $4,000, live within three weeks."
)
TALLY_SAMPLE = Path(__file__).parents[1] / "tests" / "fixtures" / "tally_form_response.json"


def build_request(
    provider: str, secret: str, event_id: str, message: str, *, now: int | None = None
) -> tuple[bytes, dict[str, str]]:
    """The exact body and headers the provider would send."""
    headers = {"Content-Type": "application/json"}
    if provider == "tally":
        payload: dict[str, Any] = json.loads(TALLY_SAMPLE.read_text())
        payload["eventId"] = event_id
        body = json.dumps(payload).encode()
        return body, headers | {"Tally-Signature": sign_tally(secret, body)}
    body = json.dumps(
        {"event_id": event_id, "lead": {"message": message, "source": "webhook_demo"}}
    ).encode()
    timestamp = int(time.time()) if now is None else now
    return body, headers | {
        "X-Timestamp": str(timestamp),
        "X-Signature": sign_generic(secret, timestamp, body),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--endpoint", required=True, help="Webhook endpoint ID (whk_...).")
    parser.add_argument("--provider", choices=["generic", "tally"], default="generic")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--event-id", default=None, help="Defaults to a new random ID.")
    parser.add_argument("--message", default=SAMPLE_MESSAGE, help="generic only.")
    args = parser.parse_args()

    if not args.base_url.startswith(("http://", "https://")):
        parser.error("--base-url must start with http:// or https://")
    secret = os.environ.get("WEBHOOK_SECRET")
    if not secret:
        print("Set WEBHOOK_SECRET to the endpoint's signing secret.", file=sys.stderr)
        return 2
    event_id = args.event_id or f"evt_{uuid.uuid4().hex[:12]}"
    body, headers = build_request(args.provider, secret, event_id, args.message)
    url = f"{args.base_url.rstrip('/')}/v1/webhooks/{args.provider}/{args.endpoint}"
    # The scheme is checked above, so S310's file:// concern does not apply.
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            print(response.status, response.read().decode())
    except urllib.error.HTTPError as exc:
        print(exc.code, exc.read().decode(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
