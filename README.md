# CSV cleanup sample

A small Python example that cleans an order CSV and explains every edit.
All 24 input records are **synthetic and fictional**. Names and `example.invalid`
email addresses do not represent customers or contacts.

The included run keeps 22 rows, merges 2 exact duplicates, and flags 12 rows
for review. `changes.csv` contains 50 field edits, review notes and merge events.

## Run it

Requires Python 3.10 or newer. No packages to install.

1. Download or clone this repository and open its folder.
2. Run `python clean.py` to regenerate the two output files.
3. Run `python -m unittest -v` to check the results.

On Windows, `py -3` can replace `python` if that is your installed launcher.
Default paths are relative to the script, so running it from another folder also works.

For another input with the same schema:

```text
python clean.py --input orders.csv --output cleaned.csv --changes edits.csv
```

The command replaces the two output CSVs on each successful run. It never edits
the input, and it rejects using the input as either output. Malformed headers,
field counts or record IDs stop the run before output files are written.

## Files

| File | Purpose |
| --- | --- |
| `sample-dirty.csv` | 24 fictional source records, including a quoted multiline name |
| `clean.py` | Cleaning rules and command-line entry point |
| `sample-clean.csv` | Generated result, including source IDs and review flags |
| `changes.csv` | Generated field edits, review notes and duplicate decisions |
| `test_clean.py` | Reproducibility, audit reconciliation and edge checks |

Input columns must be in this order:

```text
record_id,customer,email,order_date,amount_usd,status
```

Each `record_id` must be unique and contain only letters, digits, underscores
or hyphens. Input accepts UTF-8 with or without a BOM. Output is UTF-8 with LF
line endings. Source line numbers refer to the starting physical CSV line.

## What changes

| Rule | Example input | Result |
| --- | --- | --- |
| Trim outer whitespace and collapse whitespace in names | ` Avery  Sample ` | `Avery Sample` |
| Lowercase the email domain, preserving the local part | `Case@EXAMPLE.INVALID` | `Case@example.invalid` |
| Convert a supported, unambiguous date | `15/10/2026` or `10/20/2026` | `2026-10-15` or `2026-10-20` |
| Format explicit USD amounts to two decimals | `$1,200.00`, `99.5`, `0` | `1200.00`, `99.50`, `0.00` |
| Normalize listed status spellings | `PAID`, `Canceled` | `paid`, `cancelled` |

Dates accept `YYYY-MM-DD`, `YYYY/MM/DD`, and slash dates where the calendar
order is unambiguous. Equal day/month values such as `01/01/2026` are safe.
`10/11/2026` stays as written because it could mean October 11 or November 10.
Invalid dates also stay as written and receive a review flag.

USD amounts accept a leading `$`, optional sign, standard comma grouping, and
up to two decimal places. Negative amounts are retained. The script does not
convert currencies or guess formats such as `1.234,50`; those values stay for review.

Only `paid`, `pending`, `refunded`, `canceled` and `cancelled` are mapped.
An unknown status such as `awaiting` stays as written. This example does not infer
that a completed order was paid.

Blank values and the markers `N/A` and `NULL` in email, date and amount columns
become blank review items. Missing names and unknown statuses are flagged too.
There is no guessed email, date or amount. Email checks only identify obvious
format problems; they do not check delivery or whether an address exists.

To change the policy, edit `STATUS_MAP`, `MISSING` or `AMOUNT`, or the small
`normalize_date` and `clean_rows` functions in `clean.py`. Rerun the tests and
review both outputs when changing a rule.

## Duplicate handling and the audit trail

Rows merge only when **all five cleaned business fields match** and neither
record has a review flag. The first record stays. Its `source_ids` and
`source_lines` retain every merged source. Shared names or emails alone do
not cause a merge. Uncertain rows stay separate, even when their values match.

In this sample, `R008` merges into `R001`, and `R009` merges into `R002`.
The two ambiguous Elm Sample records remain separate.

Each `changes.csv` event contains a record ID, source line, field, old value,
new value, action and rule. A `merged` event also names the retained record.
Multiple edits to one field appear in execution order, so they can be replayed.
Review events keep the current value in both value columns. Added output columns
are provenance metadata, rather than inferred customer data.

The tests account for every input ID exactly once, replay every business-field
edit, reconcile review flags and merges, compare regenerated files byte for
byte, and check that the source file stays unchanged.

## Scope

This is one explicit order-data schema, not a general-purpose CSV repair tool.
Different columns or business rules need a deliberate change. Files with review
flags are usable for inspection, but still contain unresolved source values.

MIT licensed. See [LICENSE](LICENSE).
