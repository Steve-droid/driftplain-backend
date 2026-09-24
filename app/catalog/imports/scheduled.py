"""Operator CLI: health by default; explicit bounded refresh/review/reversal actions."""

import argparse
import json
import os
import signal
from dataclasses import asdict

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.catalog.imports.registry import source_ids
from app.catalog.imports.refresh import (
    refresh_source,
    review_report,
    select_snapshot,
    source_health,
)


def _timeout(*_):
    raise TimeoutError("catalog operation deadline")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=source_ids(), required=True)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--refresh", action="store_true")
    actions.add_argument("--select-snapshot", type=int)
    actions.add_argument(
        "--review-report", help="Exact acquired SHA-256, human attestation only"
    )
    parser.add_argument(
        "--snapshot", type=int, help="Current accepted snapshot for report review"
    )
    parser.add_argument(
        "--reason", help="Operator identity and reviewed reason; no credentials"
    )
    args = parser.parse_args(argv)
    if (args.select_snapshot is not None or args.review_report) and not args.reason:
        parser.error("operator actions require --reason")
    if args.review_report and args.snapshot is None:
        parser.error("--review-report requires --snapshot")
    if (args.snapshot is not None and not args.review_report) or (
        args.reason and not (args.review_report or args.select_snapshot is not None)
    ):
        parser.error("review/reversal arguments require the corresponding action")
    engine = None
    previous = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(240)
    try:
        engine = create_engine(
            os.environ["DATABASE_URL"],
            pool_size=1,
            max_overflow=0,
            pool_timeout=5,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=15000 -c lock_timeout=2000",
            },
        )
        if args.refresh:
            result = asdict(refresh_source(engine, args.source))
        elif args.select_snapshot is not None:
            select_snapshot(engine, args.source, args.select_snapshot, args.reason)
            result = {"status": "selected", "snapshot_id": args.select_snapshot}
        elif args.review_report:
            review_report(
                engine, args.source, args.review_report, args.snapshot, args.reason
            )
            result = {"status": "reviewed", "snapshot_id": args.snapshot}
        else:
            with Session(engine) as db:
                result = source_health(db, args.source)
        print(json.dumps(result, default=str))
        return 1 if result.get("status") == "failed" else 0
    except (ValueError, KeyError, OSError, SQLAlchemyError):
        print(
            json.dumps({"status": "failed", "failure_code": "catalog_operation_failed"})
        )
        return 1
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        if engine:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
