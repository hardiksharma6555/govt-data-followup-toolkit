"""
Stage 4: Standalone WhatsApp sender. Drives its own Chrome window via
Selenium - no dependency on any AI assistant or browser extension. Run this
yourself, on your own schedule:

    python3 scripts/send_whatsapp.py                     # dry run (default, sends nothing)
    python3 scripts/send_whatsapp.py --live               # actually sends messages
    python3 scripts/send_whatsapp.py --live --limit 5      # send only the first 5 pending

First run opens a Chrome window and shows a WhatsApp Web QR code - scan it
once with your phone (WhatsApp > Settings > Linked Devices > Link a Device).
The login is cached in scripts/chrome_profile/ (gitignored - this holds your
live session, treat it like a password), so later runs skip the QR step.

Safety behaviour, by design:
  - Defaults to --dry-run: opens each chat and logs what WOULD be sent, but
    never clicks Send, unless you pass --live explicitly.
  - Idempotent: every attempt (sent/failed/dry_run) is recorded in
    output/send_log.csv by mobile number. Already-"sent" numbers are skipped
    on reruns, so re-running the script never double-sends.
  - Randomized delay between messages and a hard daily send cap, to reduce
    the chance WhatsApp flags the account for bulk/bot-like sending. See
    README "WhatsApp ToS and account-risk warning" before raising these.
"""
import argparse
import csv
import random
import time
import urllib.parse
from datetime import date, datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE = Path(__file__).resolve().parent.parent
LOG_FIELDS = ["mobile", "school_raw", "principal_name", "status", "timestamp", "note"]


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


def make_driver(profile_dir: Path):
    profile_dir.mkdir(parents=True, exist_ok=True)
    opts = Options()
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--profile-directory=Default")
    driver = webdriver.Chrome(options=opts)
    driver.set_window_size(1400, 900)
    return driver


def wait_for_login(driver, timeout=180):
    driver.get("https://web.whatsapp.com")
    try:
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, '//div[@id="pane-side"]')))
        print("Already logged in.")
        return
    except TimeoutException:
        pass
    print(f"Scan the QR code in the opened Chrome window (waiting up to {timeout}s)...")
    WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.XPATH, '//div[@id="pane-side"]')))
    print("Logged in.")


def open_chat(driver, mobile: str, text: str, timeout=25):
    url = f"https://web.whatsapp.com/send?phone={mobile}&text={urllib.parse.quote(text)}"
    driver.get(url)

    invalid_xpath = '//*[contains(text(), "Phone number shared via url is invalid")]'
    send_btn_xpath = '//button[@aria-label="Send"] | //span[@data-icon="send"]/ancestor::button'

    end = time.time() + timeout
    while time.time() < end:
        try:
            driver.find_element(By.XPATH, invalid_xpath)
            return None, "invalid phone number (WhatsApp rejected it)"
        except NoSuchElementException:
            pass
        try:
            btn = driver.find_element(By.XPATH, send_btn_xpath)
            if btn.is_displayed():
                return btn, None
        except NoSuchElementException:
            pass
        time.sleep(0.5)
    return None, "timed out waiting for chat to load"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=BASE / "output" / "whatsapp_messages.csv", help="Messages CSV from generate_messages.py")
    ap.add_argument("--log", type=Path, default=BASE / "output" / "send_log.csv")
    ap.add_argument("--profile-dir", type=Path, default=BASE / "scripts" / "chrome_profile")
    ap.add_argument("--live", action="store_true", help="Actually send messages (default: dry run)")
    ap.add_argument("--limit", type=int, default=None, help="Max messages to process this run")
    ap.add_argument("--min-delay", type=float, default=20, help="Minimum seconds between sends")
    ap.add_argument("--max-delay", type=float, default=45, help="Maximum seconds between sends")
    ap.add_argument("--daily-cap", type=int, default=40, help="Max messages this script will send in one calendar day")
    args = ap.parse_args()

    messages = load_messages(args.csv)
    log_rows = load_log(args.log)
    already_sent = {r["mobile"] for r in log_rows if r["status"] == "sent"}
    pending = [m for m in messages if m["mobile"] not in already_sent]
    if args.limit:
        pending = pending[: args.limit]

    print(f"{len(messages)} total messages, {len(already_sent)} already sent, {len(pending)} pending this run.")
    if not args.live:
        print("DRY RUN - no messages will actually be sent. Pass --live to send for real.\n")
    if not pending:
        print("Nothing to do.")
        return

    driver = make_driver(args.profile_dir)
    try:
        wait_for_login(driver)
        sent_today = sent_today_count(log_rows)

        for row in pending:
            if args.live and sent_today >= args.daily_cap:
                print(f"Daily cap ({args.daily_cap}) reached. Stopping - resume tomorrow by rerunning this script.")
                break

            mobile, school, principal, text = row["mobile"], row["school_raw"], row["principal_name"], row["message"]
            print(f"-> {school} / {principal} ({mobile})")

            btn, err = open_chat(driver, mobile, text)
            if err:
                print(f"   FAILED: {err}")
                append_log(args.log, {
                    "mobile": mobile, "school_raw": school, "principal_name": principal,
                    "status": "failed", "timestamp": datetime.now().isoformat(timespec="seconds"), "note": err,
                })
                continue

            if not args.live:
                print("   [dry run] would click Send here")
                append_log(args.log, {
                    "mobile": mobile, "school_raw": school, "principal_name": principal,
                    "status": "dry_run", "timestamp": datetime.now().isoformat(timespec="seconds"), "note": "",
                })
                continue

            btn.click()
            time.sleep(1.5)
            print("   sent")
            append_log(args.log, {
                "mobile": mobile, "school_raw": school, "principal_name": principal,
                "status": "sent", "timestamp": datetime.now().isoformat(timespec="seconds"), "note": "",
            })
            sent_today += 1

            delay = random.uniform(args.min_delay, args.max_delay)
            print(f"   waiting {delay:.0f}s before next message...")
            time.sleep(delay)
    finally:
        driver.quit()

    print(f"\nDone. Full history in {args.log}")


if __name__ == "__main__":
    main()
