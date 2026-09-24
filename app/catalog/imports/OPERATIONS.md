# Catalog refresh contract (B16)

September 24, 2026. Backend 1.15.0 adds an operator-only orchestration CLI and
opt-in durable metrics. Publication does not install jobs, fetch production data,
run migrations or enable execution runtimes.

## Behavior

`python -m app.catalog.imports.scheduled --source SOURCE` reads health only.
Adding `--refresh` explicitly fetches that one fixed source and writes check state.
It uses B3/B4 acquisition, schema validation and atomic promotion, without an LLM,
blob store, startup seeding or provider credentials. DATABASE_URL and libpq's
PGPASSWORD are the only database inputs; this CLI does not load API/JWT settings.

B4 `importContract.mode` takes precedence over descriptive B1 `importMode`.
Automatic sources are swe-bench-verified, deepswe-1-1, livebench-2026-06-25,
codereviewbench, realvuln-3-1-0 and testgeneval. All other sources require reviewed
manifests. RealVuln's automatic artifact remains definition-only, not scored evidence.
Pinned URLs/revisions/hashes stay pinned: checks verify availability and content of
the accepted version, not discovery of successor benchmark versions.

A scheduler invocation tries the same PostgreSQL session advisory source lock as
manual imports. Contention returns structured `status=overlap`, exit 0, without
fetching or advancing freshness. Existing manifest CLI locking behavior is retained.
Distinct sources can proceed independently. Structured changes auto-promote only
after complete validation; known historical bytes do not move the active pointer.
Failures retain the last accepted snapshot and safe failure codes.

Reports are fetched from the frozen resultArtifact URL with its citation fragment
removed. HTML/CSV/JSON/plaintext are accepted only through the existing bounded
public-HTTPS/no-redirect transport. No links are followed and no content is executed.
These checks may detect page changes rather than score changes; dynamic pages and
redirects can require operator investigation. They do not discover new revisions.
Each distinct body is preserved in append-only catalog_report_artifact with exact
URL, SHA-256, bytes and first acquisition time. Repeated bytes add no artifact.
First acquisition is conservatively pending review even when an old manifest exists.
Report validators are separate from manifest/structured validators. A 304 requires
previous acquisition; pending remains pending until explicit review.

## Bounded execution

The CLI enforces a 240-second process alarm (Unix/Linux deployment), 5-second DB
connection/pool waits, 15-second SQL statements and 2-second database lock waits.
Fetcher requests retain their B3/B4 10-second limits, at most three transient-error
attempts and bounded backoff, with up to 8 MB per artifact and source-specific caps.
The GitOps Job supplies the hard outer 300-second deadline, zero pod retries,
read-only/non-root container and CPU/memory bounds. A hard kill cannot promise
failure bookkeeping: stale successful-check age and Kubernetes job events expose it.
No recursive retry or catch-up batch is created.

## Review and active-snapshot reversal

After inspecting the exact stored report body and extracting changed results,
validate/promote a reviewed manifest through the existing importer. This remains a
human operation; use an externally deadline-bounded operator Job with the same
pinned image and database environment. Retain its existing citation/hash contracts.

Then explicitly attest that the current accepted snapshot represents the acquired
report (including a reviewed conclusion that a page change did not change scores):

```sh
python -m app.catalog.imports.scheduled --source mmlu-pro \
  --review-report EXACT_ACQUIRED_SHA256 --snapshot ACCEPTED_SNAPSHOT_ID \
  --reason 'Steve: inspected exact acquired bytes and accepted extraction'
```

The exact latest acquired hash and current source-owned accepted snapshot must
match atomically under the source lock. The command records an immutable operator
attestation; it is not an automated provenance validator and does not rewrite the
snapshot/citations. Operator identity is supplied by the trusted database operator,
not an end-user API. Never acknowledge unread bytes just to clear an alert.
New extraction promotion or active selection invalidates the previous attestation.

Select a retained accepted snapshot after operator review:

```sh
python -m app.catalog.imports.scheduled --source mmlu-pro \
  --select-snapshot SNAPSHOT_ID --reason 'Steve: reverse incorrect extraction'
```

It rejects wrong-source/unaccepted snapshots, records old/new pointers, reason and
time, and adjusts lifecycle supersession atomically. Selecting an older snapshot
does not update successful-check freshness or mutate observations, payloads,
aliases, citations or execution references. Restore the later pointer with the same
command. This is reversal by selection, never deletion/reimport. Original
activated_at/fetched_at retain their meaning; catalog_operator_action records
subsequent selections. Health's active age is since first acquisition, not selection.

## Health and monitoring

Set CATALOG_REFRESH_METRICS_ENABLED=true only after the migration and a compatible
backend deployment. Default is false, preserving the current /metrics surface.
Existing backend Prometheus scraping receives a request-local exposition generated
from durable DB rows; it works across independent jobs and gunicorn workers without
process-local gauges or a Pushgateway. Fixed source labels never contain URLs,
payloads, user data or credentials. A failed database read emits
modelmatch_catalog_health_available=0 and omits source health.

Per-source fields separate last_checked_at, last_successful_check_at, consecutive
failures, pending_review, active snapshot age, and upstream publication age.
Refresh timestamps/counters are separate from legacy manifest-import state: a local
manifest import cannot mask a missed or failed upstream check. Each timestamp is
the start of the latest completed refresh, preserving the existing lock/time contract.
Unknown timestamps/ages are omitted from metrics and null in CLI JSON, never zero.
Upstream publication time is the accepted snapshot's reported date, not an HTTP
Last-Modified header or the time we polled. Report bytes can be freshly checked
while accepted scores are old and review is pending.

missed_check means no successful check within seven hours (six-hour cadence plus
one-hour grace), including never checked. Last attempted check remains separate, so
repeated failures cannot look fresh. GitOps alerts apply only to explicitly enabled,
unsuspended sources; missing series catches disabled metrics or a hard-killed check.
A pending review is a successful acquisition, not an accepted score update.

## Storage, packaging and rollback

Migration b16c0a7a0001 follows a0b1c2d3e4f5. It adds nullable report state, immutable
report artifacts and source-bound operator audit. Existing catalog/runtime/usage
data is preserved. Database constraints bind exact byte hashes, source snapshots
and report references. Populated downgrade refuses before destructive DDL.
Restore additive schema/history; disable schedulers/metrics to roll back behavior.

The backend wheel and Docker image include app/catalog/imports plus data/catalog/b3
and b4. The Docker runtime user is UID 10001; no API server or migrations start when
invoked with the explicit module command. Agent images need no release for this
backend-only functionality.

Report artifacts are retained, with no automated deletion. Monitor database/disk
growth, especially dynamic HTML. Existing retention-report remains advisory and
does not delete evidence; destructive retention requires separate scope. Production
enablement, image pins, migrations and any imports require separate authorization.
