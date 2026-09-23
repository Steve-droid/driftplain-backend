"""Explicit operator command; validation is the default, no scheduler or seed hook."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from sqlalchemy.exc import SQLAlchemyError

from app.catalog.imports.adapters import parse
from app.catalog.imports.registry import get_source, source_ids


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=source_ids(), required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument(
        "--manifest",
        type=Path,
        help="Trusted, explicitly reviewed JSON manifest or pinned structured JSON",
    )
    inputs.add_argument(
        "--fetch", action="store_true", help="Fetch the pinned structured source"
    )
    inputs.add_argument(
        "--retention-report",
        action="store_true",
        help="Report eligible superseded snapshots; never delete",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Commit a fully validated batch to DATABASE_URL",
    )
    args = parser.parse_args(argv)
    spec = get_source(args.source)
    try:
        if args.retention_report:
            if args.promote:
                parser.error("retention reporting cannot promote or delete")
            from sqlalchemy import create_engine, select
            from sqlalchemy.orm import Session
            from app.config import get_settings
            from app.models import CatalogSource, CatalogSourceSnapshot
            from app.catalog.imports.retention import retention_candidates

            engine = create_engine(get_settings().database_url)
            try:
                with Session(engine) as db:
                    ids = retention_candidates(db, now=datetime.now(timezone.utc))
                    ids = list(
                        db.scalars(
                            select(CatalogSourceSnapshot.id)
                            .join(CatalogSource)
                            .where(
                                CatalogSource.slug == args.source,
                                CatalogSourceSnapshot.id.in_(ids),
                            )
                        )
                    )
                    print(
                        json.dumps(
                            {
                                "eligibleSnapshotIds": ids,
                                "retentionDays": 365,
                                "deletionEnabled": False,
                            }
                        )
                    )
            finally:
                engine.dispose()
            return 0
        if args.manifest:
            # Bound reads before parsing, including operator-supplied files.
            with args.manifest.open("rb") as stream:
                raw = stream.read(spec["maxPayloadBytes"] + 1)
        elif not args.promote:
            from app.catalog.imports.fetch import fetch

            if spec["importMode"] != "automatic_structured":
                raise ValueError("reviewed report requires a reviewed manifest")
            raw = fetch(
                spec["resultArtifact"],
                allowed_urls=(spec["resultArtifact"],),
                max_bytes=spec["maxPayloadBytes"],
                expected_hash=spec.get("artifactSha256"),
            ).body
        if not args.promote:
            batch = parse(args.source, raw)
            print(
                json.dumps(
                    {
                        "status": "validated",
                        "source": args.source,
                        "observations": len(batch.rows),
                        "contentHash": batch.content_hash,
                    }
                )
            )
            return 0
        from sqlalchemy import create_engine
        from app.config import get_settings
        from app.catalog.imports.pipeline import import_bytes, import_source

        engine = create_engine(get_settings().database_url)
        try:
            result = (
                import_bytes(engine, args.source, raw)
                if args.manifest
                else import_source(engine, args.source)
            )
            print(json.dumps(asdict(result)))
            return 1 if result.status == "failed" else 0
        finally:
            engine.dispose()
    except (ValueError, OSError, SQLAlchemyError) as exc:
        # Do not emit imported values, raw bodies, connection strings or arbitrary exceptions.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "failureCode": "database_unavailable"
                    if isinstance(exc, SQLAlchemyError)
                    else "invalid_source_or_payload",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
