"""
Stage 3: Match flagged rows (output of either analyzer) against the cleaned
master contact list, then build a personalized WhatsApp message for every
contact whose entity has missing/incomplete data.

Matching: exact match on normalized name first, falls back to difflib fuzzy
matching for near-misses (typos, minor spelling variants). Anything that
still doesn't match is written to unmatched.csv for manual lookup - never
silently dropped. See README "Matching caveats" before trusting a fuzzy match.

Usage:
    python3 scripts/generate_messages.py \\
        --master output/master_clean.csv \\
        --report output/missing_report.csv \\
        --task-label "Free Text Books statement for Class 9th & 10th (2026-27)"
"""
import argparse
import csv
import difflib
from collections import defaultdict
from pathlib import Path


def load_csv(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_message(principal_name: str, school_raw: str, task_label: str, per_class: dict) -> str:
    lines = [f"Namaste {principal_name} ji,"]
    lines.append(
        f"This is regarding the {task_label} for {school_raw.strip()}. "
        f"Our records show the following details have not been filled in yet:"
    )
    for klass, cats in per_class.items():
        missing, incomplete = cats["missing"], cats["incomplete"]
        parts = []
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        if incomplete:
            parts.append(f"incomplete: {', '.join(incomplete)}")
        if not parts:
            continue
        prefix = f"- Class {klass.strip()}: " if klass and klass.strip() else "- "
        lines.append(prefix + "; ".join(parts))
    lines.append("Kindly complete and submit the pending details at the earliest. Thank you.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", required=True, type=Path, help="output/master_clean.csv from clean_master.py")
    ap.add_argument("--report", required=True, type=Path, help="missing_report.csv from an analyzer script")
    ap.add_argument("--task-label", required=True, help="Human description of the task, used in the message text")
    ap.add_argument("--out-dir", type=Path, default=Path("output"))
    ap.add_argument("--fuzzy-cutoff", type=float, default=0.82, help="difflib similarity threshold, 0-1")
    args = ap.parse_args()

    master_rows = load_csv(args.master)
    master_by_norm = defaultdict(list)
    for row in master_rows:
        master_by_norm[row["school_norm"]].append(row)
    master_norms = list(master_by_norm.keys())

    report_rows = load_csv(args.report)
    flagged = [r for r in report_rows if r["missing_categories"] or r["incomplete_categories"]]

    by_school = defaultdict(dict)
    for r in flagged:
        by_school[(r["school_raw"], r["school_norm"])][r.get("class", "")] = {
            "missing": [c for c in r["missing_categories"].split("; ") if c],
            "incomplete": [c for c in r["incomplete_categories"].split("; ") if c],
        }

    messages, unmatched, no_contact = [], [], []

    for (school_raw, school_norm), per_class in by_school.items():
        candidates = master_by_norm.get(school_norm)
        match_type = "exact"
        if not candidates:
            close = difflib.get_close_matches(school_norm, master_norms, n=1, cutoff=args.fuzzy_cutoff)
            if close:
                candidates = master_by_norm[close[0]]
                match_type = f"fuzzy ({close[0]!r})"
        if not candidates:
            unmatched.append({"school_raw": school_raw, "school_norm": school_norm})
            continue

        for contact in candidates:
            mobile = contact["mobile"]
            if not mobile.isdigit() or len(mobile) < 11:
                no_contact.append({
                    "school_raw": school_raw,
                    "principal_name": contact["principal_name"],
                    "mobile_raw": mobile,
                })
                continue
            messages.append({
                "school_raw": school_raw,
                "principal_name": contact["principal_name"] or "Sir/Madam",
                "mobile": mobile,
                "match_type": match_type,
                "message": build_message(contact["principal_name"] or "Sir/Madam", school_raw, args.task_label, per_class),
            })

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows, fields in [
        ("whatsapp_messages.csv", messages, ["school_raw", "principal_name", "mobile", "match_type", "message"]),
        ("unmatched_schools.csv", unmatched, ["school_raw", "school_norm"]),
        ("no_contact_schools.csv", no_contact, ["school_raw", "principal_name", "mobile_raw"]),
    ]:
        path = args.out_dir / name
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    print(f"Messages ready to send : {len(messages)} -> {args.out_dir / 'whatsapp_messages.csv'}")
    print(f"Unmatched schools      : {len(unmatched)} -> {args.out_dir / 'unmatched_schools.csv'}")
    print(f"Matched but no contact : {len(no_contact)} -> {args.out_dir / 'no_contact_schools.csv'}")


if __name__ == "__main__":
    main()
