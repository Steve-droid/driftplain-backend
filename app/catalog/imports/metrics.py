"""Read durable catalog health at scrape time, never process-local job gauges."""

from prometheus_client import CollectorRegistry, Gauge, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.catalog.imports.refresh import all_source_health


def render_catalog_metrics(db):
    registry = CollectorRegistry()
    available = Gauge(
        "modelmatch_catalog_health_available",
        "Catalog health database read succeeded.",
        registry=registry,
    )
    fields = {
        "last_checked_at": "Start time of last completed upstream refresh; omitted before any check.",
        "last_successful_check_at": "Start time of last successful upstream refresh; omitted if unknown.",
        "failure_count": "Consecutive failed checks.",
        "pending_review": "Exact fetched report differs from the human-reviewed hash.",
        "missed_check": "No successful check within seven hours, or never checked.",
        "active_snapshot_age_seconds": "Age since accepted snapshot was first acquired, not last checked.",
        "upstream_content_age_seconds": "Age of published source content; omitted if unknown.",
    }
    # Read all rows before emitting families; errors cannot leave a partial healthy scrape.
    try:
        db.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        health = all_source_health(db)
    except SQLAlchemyError:
        db.rollback()
        available.set(0)
        return generate_latest(registry)
    available.set(1)
    for field, help_text in fields.items():
        metric = Gauge(
            "modelmatch_catalog_" + field, help_text, ["source"], registry=registry
        )
        for row in health:
            value = row[field]
            if value is not None:
                metric.labels(row["source"]).set(
                    value.timestamp() if field.endswith("_at") else value
                )
    return generate_latest(registry)
