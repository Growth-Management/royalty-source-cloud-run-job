# Phase 1 SOURCE diff worker

## Purpose

Production publication already stages a validated monthly snapshot in the US region,
compares it with `ice_qb_source_p1`, and replaces the target month transactionally
when apply is explicitly requested.

Phase 1 keeps that behavior and extracts the comparison into a reusable worker.

## Safe diff model

`wholesale_sales_report` does not have a verified unique business row key. Real
202603-202607 data contains many duplicate groups for the candidate key
`year_month, dl_year_month, billing_code, wholesale_code, product_code, contents_code`.
Therefore Phase 1 does not guess an UPDATE key.

The exact-row multiset is the canonical comparison:

- `UNCHANGED`: identical row occurrences on both sides.
- `INSERT`: validated source occurrences not present in production.
- `DELETE_CANDIDATE`: production occurrences not present in the validated source.
- `UPDATE`: 0 until a stable unique key is verified for the source kind.

## Apply policy

`DELETE_CANDIDATE` is not permission to delete. The production publisher must block
apply when a delete candidate exists. A future reviewed workflow may add an explicit,
audited override; Phase 1 does not expose that override to the normal Cloud Run Job
execution path.

## Reuse

`app/source_diff_worker.py` is intended to be reused by:

1. production publish dry-run,
2. the Streamlit admin diff view,
3. future source-kind-specific import jobs.

## Tests

Run the pure unit tests without GCP credentials:

```bash
python -m unittest discover -s tests -p "test_*.py"
```
