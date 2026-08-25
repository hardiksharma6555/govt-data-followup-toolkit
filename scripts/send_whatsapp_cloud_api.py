"""
Stage 4 (alternative to send_whatsapp.py): sends via the official WhatsApp
Cloud API instead of driving a browser. No account-ban risk, no Chrome/
Selenium dependency, works headlessly on a server/cron job - but requires
Meta Developer setup and an APPROVED MESSAGE TEMPLATE first (Cloud API
cannot send arbitrary free text to someone who hasn't messaged you in the
last 24h - only a pre-approved template). See README "Option B: WhatsApp
Cloud API" for the full one-time setup walkthrough (Meta app, test number,
template submission).

Credentials are read from environment variables (never hardcode a token):
    WHATSAPP_ACCESS_TOKEN     - from Meta App Dashboard > WhatsApp > API Setup
    WHATSAPP_PHONE_NUMBER_ID  - same page, "Phone number ID"
    WHATSAPP_TEMPLATE_NAME    - the template name you created and got approved
    WHATSAPP_TEMPLATE_LANG    - template's language code, e.g. "en_US" (default)
A .env file in the repo root (gitignored) is auto-loaded if present - copy
.env.example to .env and fill it in rather than exporting these by hand.

Usage:
    python3 scripts/send_whatsapp_cloud_api.py                  # dry run
    python3 scripts/send_whatsapp_cloud_api.py --live            # send for real
    python3 scripts/send_whatsapp_cloud_api.py --live --limit 5

By default each template call fills 4 placeholders, in order, from these
CSV columns: principal_name, task_label, school_raw, summary (matching the
4-line reminder template documented in the README). If your approved
template has a different shape, pass --template-params as a comma-separated
list of column names, in the order your template's {{1}} {{2}} ... expect.
"""
import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOG_FIELDS = ["mobile", "school_raw", "principal_name", "status", "timestamp", "note"]
API_VERSION = "v21.0"


def load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_messages(csv_path: Path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_log(log_path: Path):
    if not log_path.exists():
        return []
    with open(log_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_log(log_path: Path, row: dict):
    is_new = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if is_new:
            w.writeheader()
        w.writerow(row)


def sent_today_count(log_rows):
    today = date.today().isoformat()
    return sum(1 for r in log_rows if r["status"] == "sent" and r["timestamp"].startswith(today))


def build_payload(mobile: str, template_name: str, template_lang: str, params: list[str]) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": mobile,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": template_lang},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in params],
            }],
        },
    }


def send(payload: dict, phone_number_id: str, access_token: str) -> tuple[bool, str]:
    url = f"https://graph.facebook.com/{API_VERSION}/{phone_number_id}/messages"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
            return True, body.get("messages", [{}])[0].get("id", "")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        try:
            msg = json.loads(err_body)["error"]["message"]
        except Exception:
            msg = err_body
        return False, msg


def main():
    load_dotenv(BASE / ".env")

    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=BASE / "output" / "whatsapp_messages.csv")
    ap.add_argument("--log", type=Path, default=BASE / "output" / "send_log_cloud_api.csv")
    ap.add_argument("--live", action="store_true", help="Actually send messages (default: dry run)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls")
    ap.add_argument("--daily-cap", type=int, default=None, help="Optional max sends per calendar day")
    ap.add_argument(
        "--template-params", default="principal_name,task_label,school_raw,summary",
        help="Comma-separated CSV column names, in the order your approved template's {{1}} {{2}}... expect",
    )
    args = ap.parse_args()

    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    template_name = os.environ.get("WHATSAPP_TEMPLATE_NAME")
    template_lang = os.environ.get("WHATSAPP_TEMPLATE_LANG", "en_US")
    missing_env = [
        name for name, val in [
            ("WHATSAPP_ACCESS_TOKEN", access_token),
            ("WHATSAPP_PHONE_NUMBER_ID", phone_number_id),
            ("WHATSAPP_TEMPLATE_NAME", template_name),
        ] if not val
    ]
    if missing_env:
        raise SystemExit(
            f"Missing required environment variable(s): {', '.join(missing_env)}.\n"
            f"Copy .env.example to .env and fill it in, or export them directly. See README."
        )

    param_cols = [c.strip() for c in args.template_params.split(",") if c.strip()]

    messages = load_messages(args.csv)
    log_rows = load_log(args.log)
    already_sent = {r["mobile"] for r in log_rows if r["status"] == "sent"}
    pending = [m for m in messages if m["mobile"] not in already_sent]
    if args.limit:
        pending = pending[: args.limit]

    print(f"{len(messages)} total messages, {len(already_sent)} already sent, {len(pending)} pending this run.")
    print(f"Template: {template_name!r} ({template_lang}), params from columns {param_cols}")
    if not args.live:
        print("DRY RUN - no messages will actually be sent. Pass --live to send for real.\n")
    if not pending:
        print("Nothing to do.")
        return

    sent_today = sent_today_count(log_rows)
    for row in pending:
        if args.live and args.daily_cap and sent_today >= args.daily_cap:
            print(f"Daily cap ({args.daily_cap}) reached. Stopping - resume by rerunning this script.")
            break

        mobile, school, principal = row["mobile"], row["school_raw"], row["principal_name"]
        try:
            params = [row[c] for c in param_cols]
        except KeyError as e:
            raise SystemExit(f"--template-params references column {e} which isn't in {args.csv}") from None

        payload = build_payload(mobile, template_name, template_lang, params)
        print(f"-> {school} / {principal} ({mobile})")

        if not args.live:
            print(f"   [dry run] would POST: {json.dumps(payload)}")
            append_log(args.log, {
                "mobile": mobile, "school_raw": school, "principal_name": principal,
                "status": "dry_run", "timestamp": datetime.now().isoformat(timespec="seconds"), "note": "",
            })
            continue

        ok, info = send(payload, phone_number_id, access_token)
        if ok:
            print(f"   sent (message id {info})")
            append_log(args.log, {
                "mobile": mobile, "school_raw": school, "principal_name": principal,
                "status": "sent", "timestamp": datetime.now().isoformat(timespec="seconds"), "note": info,
            })
            sent_today += 1
        else:
            print(f"   FAILED: {info}")
            append_log(args.log, {
                "mobile": mobile, "school_raw": school, "principal_name": principal,
                "status": "failed", "timestamp": datetime.now().isoformat(timespec="seconds"), "note": info,
            })

        time.sleep(args.delay)

    print(f"\nDone. Full history in {args.log}")


if __name__ == "__main__":
    main()
