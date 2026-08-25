# Government Data Follow-Up Toolkit

Cross-reference a master contact list (e.g. school principals) against a
criteria file that tracks some task (e.g. submitted textbook counts,
attendance registers, fee receipts) - find who hasn't submitted their data,
and send each of them a personalized WhatsApp follow-up automatically from
your own WhatsApp account.

Built for a real use case: a district education office needed to nudge
school principals who hadn't filled in a government data return, by
WhatsApp, using the office's own number. It's written generically so you can
point it at any "master list of people" + "criteria file with some blank
cells" pair.

## How it works

```
master contact list (.xlsx)         criteria file (.csv or .xlsx)
        |                                    |
   clean_master.py                  analyze_generic.py
        |                          (or analyze_complex_example.py
        |                           for multi-row-header layouts)
        v                                    v
  output/master_clean.csv       output/missing_report.csv
        \                                   /
         \                                 /
          v                               v
             generate_messages.py
                      |
                      v
        output/whatsapp_messages.csv  (+ unmatched/no-contact reports)
                      |
                      v
              send_whatsapp.py
        (opens your real WhatsApp Web session,
         sends each message, throttled)
```

Four independent stages, each a plain Python script that reads one CSV and
writes another. Run them one at a time and inspect the output before moving
to the next - nothing sends a message until you explicitly run the last
stage with `--live`.

## Requirements

- Python 3.9+
- Google Chrome installed
- A WhatsApp account on your phone (to scan the QR code once)

```bash
pip install -r requirements.txt
```

## Quick start (using the included synthetic sample data)

The repo ships with fake schools, fake names, and fake phone numbers
(`data/sample_*`) so you can run the entire pipeline immediately, with zero
setup, and see exactly what it does before pointing it at real data.

```bash
# 1. Clean the master contact list into a canonical CSV
python3 scripts/clean_master.py --input data/sample_master_contacts.xlsx

# 2. Find missing fields in the criteria file
python3 scripts/analyze_generic.py --input data/sample_criteria_flat.csv

# 3. Match the two and build personalized messages
python3 scripts/generate_messages.py \
    --master output/master_clean.csv \
    --report output/missing_report.csv \
    --task-label "Quarterly School Records Submission"

# 4. Review before sending anything real
cat output/whatsapp_messages.csv
cat output/unmatched_schools.csv     # schools with missing data but no contact match
cat output/no_contact_schools.csv    # matched, but no valid phone number on file

# 5. Dry run - opens each WhatsApp chat, never clicks Send
python3 scripts/send_whatsapp.py --limit 1

# 6. Only once you've checked step 5's output: send for real
python3 scripts/send_whatsapp.py --live --limit 1
```

Step 5's first run opens a Chrome window with a WhatsApp Web QR code - scan
it with **Settings > Linked Devices > Link a Device** on your phone. The
login is cached in `scripts/chrome_profile/` (gitignored), so you only do
this once.

## Using your own data

### 1. Master contact list (`--input your_master.xlsx`)

An Excel file with one row per person/office, containing at minimum a
name/identifier column and a mobile number column. `clean_master.py`
auto-detects the header row (scanning the first 15 rows for cells matching
keywords like "school", "principal", "mobile", "phone") - it does **not**
need to be row 1, and a blank separator row between the header and the data
is handled automatically. If auto-detection fails on your file, pass exact
header text:

```bash
python3 scripts/clean_master.py --input your_master.xlsx \
    --school-col "Institution Name" --principal-col "Head" --mobile-col "Contact No"
```

Output: `output/master_clean.csv` with `school_raw, school_norm, principal_name, mobile`.
Anything that isn't a clean 10-digit number (vacant posts, typos, text like
"NA") is flagged in the console output and later skipped at send time rather
than silently dropped.

### 2. Criteria file - two options depending on your layout

**Flat layout** (most common: one row per entity, some columns blank) - use
`analyze_generic.py` directly:

```bash
python3 scripts/analyze_generic.py --input your_criteria.csv
```

Works for `.csv` or `.xlsx`. Column 1 is the identifier (must match the
master list's name column); every other column is treated as a required
field, and a blank cell in it is reported as missing.

**Complex multi-row-header layout** (merged category groups, sub-columns -
common in government returns) - `scripts/analyze_complex_example.py` is a
worked reference for exactly this shape, not a general tool (these layouts
vary too much to auto-detect). Copy it, rename it, and edit `CATEGORY_GROUPS`
/ `DATA_START_ROW` / `SCHOOL_COL` / `CLASS_COL` at the top to match your
sheet - see the comments in that file for the expected header shape. Whatever
you write must produce a CSV with these columns:

| column | meaning |
|---|---|
| `school_raw` | entity name as it appears in the criteria file |
| `school_norm` | normalized name for matching (use `common.normalize_name`) |
| `class` | optional sub-grouping (e.g. grade); leave blank if not applicable |
| `missing_categories` | `; `-separated list of fully-blank fields |
| `incomplete_categories` | `; `-separated list of partially-filled fields |

That contract is all `generate_messages.py` needs - write any analyzer you
like as long as it produces this shape.

### 3. Matching caveats

`generate_messages.py` matches on normalized names: exact match first, then
a fuzzy fallback (`difflib`, default cutoff `0.82`) for typos/spelling
variants. Two things to know before trusting the output:

- **Only strip tokens that are redundant on both files.** The default
  `--strip-tokens GSSS,GSS` assumes your master list is exclusively one
  school type. If your criteria file mixes school types (e.g. GSSS and GHS
  in the same list) and your master only covers one, **do not** add that
  other type's token to the strip list - two different schools can share a
  village name (`"GHS Sahoura"` vs `"Sahoura"`/GSSS Sahoura are different
  schools), and stripping the type prefix would silently merge them,
  sending your message to the wrong person. Leave unmatched entries in
  `unmatched_schools.csv` for manual lookup instead.
- **Always eyeball `match_type` in `whatsapp_messages.csv`** for any row
  marked `fuzzy (...)` before sending. Fuzzy matching is inherently
  approximate.

### 4. Sending

```bash
python3 scripts/send_whatsapp.py                    # dry run, default
python3 scripts/send_whatsapp.py --live              # send for real
python3 scripts/send_whatsapp.py --live --limit 5    # only the next 5 pending
```

Re-running the script is always safe: every attempt is logged to
`output/send_log.csv` by phone number, and anything already marked `sent` is
skipped - it will never double-send. Useful flags:

| flag | default | purpose |
|---|---|---|
| `--csv` | `output/whatsapp_messages.csv` | which messages file to send |
| `--min-delay` / `--max-delay` | `20` / `45` (seconds) | randomized pause between sends |
| `--daily-cap` | `40` | max sends per calendar day before it stops itself |
| `--profile-dir` | `scripts/chrome_profile` | where the WhatsApp Web login is cached |

## WhatsApp ToS and account-risk warning

This drives WhatsApp Web the same way a human clicking through the UI would
- there is no official API involved. WhatsApp's terms of service prohibit
bulk/automated messaging, and accounts sending many messages in a short
window (especially to numbers that haven't messaged you first) can be
rate-limited or banned. The delay/cap defaults exist to reduce that risk,
not eliminate it. Recommendations:

- Test on your own number or a colleague's first (see Quick Start).
- Keep `--daily-cap` low, especially for the first few runs.
- Don't drop the randomized delay to zero.
- Consider whether the official [WhatsApp Business Cloud
  API](https://developers.facebook.com/docs/whatsapp/cloud-api) is a better
  fit if you need this at real scale or need guaranteed delivery - it
  requires business verification and template approval, but carries no
  account-ban risk.

## Privacy

- `.gitignore` excludes every real data file (`data/*.xlsx`, `data/*.csv`
  other than the `sample_*` files), all of `output/`, and
  `scripts/chrome_profile/` (your live WhatsApp session - treat it like a
  password). Don't force-add these.
- Never commit real names or phone numbers to this or any repo, private or
  public - repo visibility can change, and git history is hard to fully
  scrub after the fact.

## Troubleshooting

- **`SessionNotCreatedException: Chrome instance exited`** - usually a
  leftover Chrome process holding a lock on `scripts/chrome_profile/` from a
  previous run that didn't exit cleanly. Fix: `pkill -f chrome_profile` then
  re-run.
- **QR code keeps expiring** - the wait timeout is 180s by default; if your
  phone camera is slow to scan, just re-run the script, a new QR loads each
  time.
- **A message opens a chat with no text pre-filled** - WhatsApp Web
  occasionally drops the `text=` URL param for very long messages; keep
  messages under ~1000 characters.

## Regenerating the sample data

`data/sample_*` files are generated by `scripts/make_sample_data.py` - all
values are fictional. Re-run it any time to reset the demo data:

```bash
python3 scripts/make_sample_data.py
```
