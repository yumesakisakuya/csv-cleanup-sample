"""Clean the fictional order sample using explicit rules and a field-level audit log."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import date
from decimal import Decimal

FIELDS = ("record_id", "customer", "email", "order_date", "amount_usd", "status")
OUTPUT_FIELDS = (*FIELDS, "source_ids", "source_lines", "review_fields")
LOG_FIELDS = ("record_id", "source_line", "field", "old_value", "new_value",
              "action", "rule", "kept_record_id")
STATUS_MAP = {"paid": "paid", "pending": "pending", "canceled": "cancelled",
              "cancelled": "cancelled", "refunded": "refunded"}
MISSING = {"", "n/a", "null"}
AMOUNT = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?\Z")


def normalize_date(value: str) -> tuple[str, str]:
    """Accept year-first dates and unambiguous slash dates, without a locale guess."""
    year_first = re.fullmatch(r"(\d{4})([-/])(\d{2})\2(\d{2})", value)
    short_first = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    try:
        if year_first:
            return date(int(year_first[1]), int(year_first[3]), int(year_first[4])).isoformat(), ""
        if short_first:
            a, b, year = map(int, short_first.groups())
            if 1 <= a <= 12 and 1 <= b <= 12 and a != b:
                return value, "ambiguous_date"
            month, day = (b, a) if a > 12 else (a, b)
            return date(year, month, day).isoformat(), ""
    except ValueError:
        return value, "invalid_date"
    return value, "unsupported_date"


def read_source(filename: Path) -> list[tuple[int, dict[str, str]]]:
    rows, seen_ids = [], set()
    with filename.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("Input headers must be: " + ",".join(FIELDS))
        last_line = reader.line_num
        for row in reader:
            first_line, last_line = last_line + 1, reader.line_num
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Wrong field count near source line {first_line}")
            record_id = row["record_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", record_id) or record_id in seen_ids:
                raise ValueError(f"Missing, invalid or repeated record_id near line {first_line}")
            seen_ids.add(record_id)
            rows.append((first_line, row))
    return rows


def clean_rows(source: list[tuple[int, dict[str, str]]]) -> tuple[list[dict], list[dict]]:
    output, changes, seen = [], [], {}
    for line, original in source:
        row, reviews = dict(original), []

        def log(field: str, old: str, new: str, action: str, rule: str, kept: str = ""):
            changes.append(dict(zip(LOG_FIELDS, (original["record_id"], str(line), field,
                                                old, new, action, rule, kept))))

        def change(field: str, new: str, rule: str):
            if row[field] != new:
                log(field, row[field], new, "changed", rule)
                row[field] = new

        def review(field: str, rule: str):
            reviews.append(f"{field}:{rule}")
            log(field, row[field], row[field], "review", rule)

        for field in FIELDS[1:]:
            change(field, row[field].strip(), "trim_outer_whitespace")
        change("customer", " ".join(row["customer"].split()), "collapse_name_whitespace")
        if not row["customer"]:
            review("customer", "missing_customer")

        for field in ("email", "order_date", "amount_usd"):
            if row[field].casefold() in MISSING:
                change(field, "", "missing_marker_to_blank")
                review(field, "missing_value")

        if row["email"]:
            if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", row["email"]):
                local, domain = row["email"].rsplit("@", 1)
                change("email", local + "@" + domain.lower(), "lowercase_email_domain")
            else:
                review("email", "invalid_email_shape")
        if row["order_date"]:
            normalized, issue = normalize_date(row["order_date"])
            if issue:
                review("order_date", issue)
            else:
                change("order_date", normalized, "unambiguous_date_to_iso")
        if row["amount_usd"]:
            amount = row["amount_usd"].removeprefix("$").strip()
            if AMOUNT.fullmatch(amount):
                change("amount_usd", format(Decimal(amount.replace(",", "")), ".2f"),
                       "explicit_usd_number_format")
            else:
                review("amount_usd", "unsupported_amount_format")
        status = STATUS_MAP.get(row["status"].casefold())
        if status is None:
            review("status", "unknown_status")
        else:
            change("status", status, "documented_status_map")

        key = tuple(row[field] for field in FIELDS[1:])
        if not reviews and key in seen:
            kept = seen[key]
            kept["source_ids"] += ";" + row["record_id"]
            kept["source_lines"] += ";" + str(line)
            log("__row__", json.dumps(row, ensure_ascii=False, sort_keys=True),
                kept["record_id"], "merged", "exact_valid_business_fields", kept["record_id"])
        else:
            row.update(source_ids=row["record_id"], source_lines=str(line),
                       review_fields=";".join(reviews))
            output.append(row)
            if not reviews:
                seen[key] = row
    return output, changes


def csv_text(fields: tuple[str, ...], rows: list[dict]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def run(input_path: Path, output_path: Path, changes_path: Path) -> dict[str, int]:
    paths = [p.resolve() for p in (input_path, output_path, changes_path)]
    if len(set(paths)) != 3:
        raise ValueError("Input, output and changes paths must be different")
    if any(p.suffix.lower() != ".csv" for p in (output_path, changes_path)):
        raise ValueError("Output and changes paths must have a .csv extension")
    source = read_source(input_path)
    output, changes = clean_rows(source)
    # Prepare both files before replacing either destination; the input stays untouched.
    prepared = []
    try:
        for destination, content in ((output_path, csv_text(OUTPUT_FIELDS, output)),
                                     (changes_path, csv_text(LOG_FIELDS, changes))):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                             dir=destination.parent, delete=False) as temp:
                prepared.append((Path(temp.name), destination))
                temp.write(content)
        for temporary, destination in prepared:
            os.replace(temporary, destination)
    finally:
        for temporary, _ in prepared:
            temporary.unlink(missing_ok=True)
    return {"input_rows": len(source), "output_rows": len(output),
            "merged_rows": sum(c["action"] == "merged" for c in changes),
            "rows_needing_review": sum(bool(r["review_fields"]) for r in output),
            "logged_events": len(changes)}


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=root / "sample-dirty.csv")
    parser.add_argument("--output", type=Path, default=root / "sample-clean.csv")
    parser.add_argument("--changes", type=Path, default=root / "changes.csv")
    args = parser.parse_args()
    try:
        summary = run(args.input, args.output, args.changes)
    except (ValueError, OSError, csv.Error) as error:
        parser.exit(2, f"Cannot clean input: {error}\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
