"""Independent reconciliation and edge checks for the committed sample."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import clean

ROOT = Path(__file__).resolve().parent


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.source = clean.read_source(ROOT / "sample-dirty.csv")
        self.output, self.events = clean.clean_rows(self.source)
        self.by_id = {row["record_id"]: row for row in self.output}

    def test_reproducible_files_and_unchanged_source(self):
        source = ROOT / "sample-dirty.csv"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for suffix in ("first", "second"):
                summary = clean.run(source, folder / (suffix + ".csv"),
                                    folder / (suffix + "-changes.csv"))
                self.assertEqual(summary["input_rows"], 24)
                self.assertEqual(summary["output_rows"], 22)
                self.assertEqual(summary["merged_rows"], 2)
            self.assertEqual((folder / "first.csv").read_bytes(),
                             (folder / "second.csv").read_bytes())
            self.assertEqual((folder / "first-changes.csv").read_bytes(),
                             (folder / "second-changes.csv").read_bytes())
            self.assertEqual((folder / "first.csv").read_bytes(),
                             (ROOT / "sample-clean.csv").read_bytes())
            self.assertEqual((folder / "first-changes.csv").read_bytes(),
                             (ROOT / "changes.csv").read_bytes())
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_every_source_has_exactly_one_destination(self):
        original = {row["record_id"]: line for line, row in self.source}
        lineage = []
        for row in self.output:
            ids, lines = row["source_ids"].split(";"), row["source_lines"].split(";")
            self.assertEqual(len(ids), len(lines))
            for record_id, line in zip(ids, lines):
                self.assertEqual(original[record_id], int(line))
                lineage.append(record_id)
        self.assertCountEqual(lineage, original)
        merges = [e for e in self.events if e["action"] == "merged"]
        self.assertEqual({e["record_id"]: e["kept_record_id"] for e in merges},
                         {"R008": "R001", "R009": "R002"})
        self.assertEqual(len(lineage), len(set(lineage)))

    def test_log_replays_every_business_field_change(self):
        replay = {row["record_id"]: dict(row) for _, row in self.source}
        source_lines = {row["record_id"]: str(line) for line, row in self.source}
        merged, review_flags = {}, {}
        for event in self.events:
            rid, field = event["record_id"], event["field"]
            self.assertEqual(event["source_line"], source_lines[rid])
            if event["action"] == "changed":
                self.assertNotEqual(event["old_value"], event["new_value"])
                self.assertEqual(replay[rid][field], event["old_value"])
                replay[rid][field] = event["new_value"]
            elif event["action"] == "review":
                self.assertEqual(event["old_value"], replay[rid][field])
                self.assertEqual(event["old_value"], event["new_value"])
                review_flags.setdefault(rid, []).append(field + ":" + event["rule"])
            elif event["action"] == "merged":
                self.assertEqual(json.loads(event["old_value"]), replay[rid])
                merged[rid] = event["kept_record_id"]
            else:
                self.fail("Unexpected audit action")
        for rid, expected in replay.items():
            kept = self.by_id[merged.get(rid, rid)]
            for field in clean.FIELDS[1:]:
                self.assertEqual(expected[field], kept[field], (rid, field))
            if rid not in merged:
                self.assertEqual(kept["review_fields"], ";".join(review_flags.get(rid, [])))
        self.assertEqual(set(replay), set(self.by_id) | set(merged))

    def test_uncertain_values_are_preserved_and_not_merged(self):
        for rid in ("R005", "R022"):
            self.assertEqual(self.by_id[rid]["order_date"], "10/11/2026")
            self.assertIn("order_date:ambiguous_date", self.by_id[rid]["review_fields"])
        self.assertEqual(self.by_id["R007"]["amount_usd"], "")
        self.assertEqual(self.by_id["R012"]["amount_usd"], "1.234,50")
        self.assertEqual(self.by_id["R018"]["amount_usd"], "NaN")
        self.assertEqual(self.by_id["R015"]["status"], "awaiting")
        self.assertEqual(self.by_id["R013"]["amount_usd"], "0.00")
        self.assertEqual(self.by_id["R014"]["amount_usd"], "-25.00")
        self.assertEqual(self.by_id["R024"]["customer"], "Willow Sample")

    def test_date_boundaries_and_email_local_part(self):
        for value, expected in [("15/10/2026", "2026-10-15"),
                                ("10/20/2026", "2026-10-20"),
                                ("01/01/2026", "2026-01-01"),
                                ("2024-02-29", "2024-02-29")]:
            self.assertEqual(clean.normalize_date(value), (expected, ""))
        self.assertEqual(clean.normalize_date("2026-02-29")[1], "invalid_date")
        self.assertEqual(clean.normalize_date("09/10/2026")[1], "ambiguous_date")
        row = dict(zip(clean.FIELDS, ("X1", "Example", "Case@EXAMPLE.INVALID",
                                     "2026-10-01", "0", "paid")))
        output, _ = clean.clean_rows([(2, row)])
        self.assertEqual(output[0]["email"], "Case@example.invalid")

    def test_invalid_source_does_not_create_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bad = folder / "bad.csv"
            for text in ("wrong,headers\na,b\n",
                         ",".join(clean.FIELDS) + "\nX1,Name,a@b.invalid,2026-10-01,1,paid,extra\n",
                         ",".join(clean.FIELDS) + "\nX1,Name,a@b.invalid,2026-10-01,1,paid\n" * 2):
                bad.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    clean.run(bad, folder / "out.csv", folder / "log.csv")
                self.assertFalse((folder / "out.csv").exists())
                self.assertFalse((folder / "log.csv").exists())

    def test_output_cannot_replace_input_and_cli_error_is_clear(self):
        source = ROOT / "sample-dirty.csv"
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "out.csv"
            with self.assertRaises(ValueError):
                clean.run(source, source, out)
            with self.assertRaises(ValueError):
                clean.run(source, out, out)
            result = subprocess.run([sys.executable, str(ROOT / "clean.py"),
                                     "--input", str(source), "--output", str(source),
                                     "--changes", str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must be different", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
