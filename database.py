"""
database.py
Data-access layer for client records.

All public functions accept an org_id so every query is tenant-scoped.
The storage backend is SQLAlchemy (SQLite for local dev, PostgreSQL in prod).
"""

import logging
from pathlib import Path

import pandas as pd

from models import Client, db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column alias map  (normalised key → canonical field name)
# ---------------------------------------------------------------------------
_COLUMN_ALIASES: dict[str, str] = {
    "id":                   "client_id",
    "client_id":            "client_id",
    "clientid":             "client_id",
    "customer_id":          "client_id",
    "name":                 "client_name",
    "client_name":          "client_name",
    "full_name":            "client_name",
    "customer_name":        "client_name",
    "address":              "address",
    "property_address":     "address",
    "prop_address":         "address",
    "loan":                 "loan_balance",
    "balance":              "loan_balance",
    "loan_balance":         "loan_balance",
    "loan_amount":          "loan_balance",
    "outstanding_balance":  "loan_balance",
    "rate":                 "current_rate",
    "interest_rate":        "current_rate",
    "current_rate":         "current_rate",
    "ir":                   "current_rate",
    "broker":               "broker",
    "broker_name":          "broker",
    "adviser":              "broker",
    "advisor":              "broker",
    "expiry":               "fixed_expiry",
    "fixed_expiry":         "fixed_expiry",
    "fixed_rate_expiry":    "fixed_expiry",
    "expiry_date":          "fixed_expiry",
    "rate_expiry":          "fixed_expiry",
}

CANONICAL_COLUMNS = [
    "client_id", "client_name", "address",
    "loan_balance", "current_rate", "broker", "fixed_expiry",
]


# ---------------------------------------------------------------------------
# Column normalisation
# ---------------------------------------------------------------------------

def normalise_columns(columns: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col in columns:
        key = col.strip().lower().replace(" ", "_").replace("-", "_")
        canonical = _COLUMN_ALIASES.get(key)
        if canonical:
            mapping[col] = canonical
    return mapping


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_all_clients(org_id: int) -> list[dict]:
    rows = Client.query.filter_by(org_id=org_id).order_by(Client.client_id).all()
    return [r.to_dict() for r in rows]


def get_client_count(org_id: int) -> int:
    return Client.query.filter_by(org_id=org_id).count()


# ---------------------------------------------------------------------------
# Upsert (single record)
# ---------------------------------------------------------------------------

def upsert_client(org_id: int, record: dict) -> str:
    """
    Insert or update a client scoped to org_id.
    Returns 'added' or 'updated'.
    """
    cid = str(record.get("client_id", "")).strip()
    if not cid:
        raise ValueError("Record is missing a non-empty client_id.")

    fields = {col: str(record.get(col, "") or "") for col in CANONICAL_COLUMNS}

    existing = Client.query.filter_by(org_id=org_id, client_id=cid).first()

    if existing:
        existing.client_name  = fields["client_name"]
        existing.address      = fields["address"]
        existing.loan_balance = fields["loan_balance"]
        existing.current_rate = fields["current_rate"]
        existing.broker       = fields["broker"]
        existing.fixed_expiry = fields["fixed_expiry"]
        return "updated"
    else:
        client = Client(
            org_id=org_id,
            client_id=cid,
            client_name=fields["client_name"],
            address=fields["address"],
            loan_balance=fields["loan_balance"],
            current_rate=fields["current_rate"],
            broker=fields["broker"],
            fixed_expiry=fields["fixed_expiry"],
        )
        db.session.add(client)
        return "added"


# ---------------------------------------------------------------------------
# Seed (runs once per org when their client table is empty)
# ---------------------------------------------------------------------------

def seed_from_csv(org_id: int, csv_path: Path) -> None:
    if get_client_count(org_id) > 0:
        return
    logger.info("Seeding org %d from %s", org_id, csv_path)
    try:
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    except Exception as exc:
        logger.error("Could not read seed CSV: %s", exc)
        return
    added, _updated, errors = _ingest_dataframe(org_id, df)
    logger.info("Seed complete – org %d: %d added, %d errors.", org_id, added, errors)


# ---------------------------------------------------------------------------
# Upload processing
# ---------------------------------------------------------------------------

def process_upload(file_storage, org_id: int) -> dict:
    """
    Parse a Flask FileStorage (CSV or Excel), upsert every row under org_id,
    and return {"added", "updated", "errors", "total"}.
    Raises ValueError for structural problems.
    """
    filename = (file_storage.filename or "").lower()

    if filename.endswith(".csv"):
        try:
            df = pd.read_csv(file_storage, dtype=str, keep_default_na=False)
        except Exception as exc:
            raise ValueError(f"Could not parse CSV: {exc}") from exc
    elif filename.endswith((".xlsx", ".xls")):
        try:
            df = pd.read_excel(file_storage, dtype=str)
            df = df.fillna("")
        except Exception as exc:
            raise ValueError(f"Could not parse Excel file: {exc}") from exc
    else:
        raise ValueError("Unsupported file type. Please upload a .csv or .xlsx file.")

    added, updated, errors = _ingest_dataframe(org_id, df)
    logger.info(
        "Upload complete – org %d: added %d, updated %d, errors %d",
        org_id, added, updated, errors,
    )
    return {"added": added, "updated": updated, "errors": errors, "total": added + updated}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ingest_dataframe(org_id: int, df: pd.DataFrame) -> tuple[int, int, int]:
    df.columns = [str(c) for c in df.columns]
    rename_map = normalise_columns(list(df.columns))
    df = df.rename(columns=rename_map)

    if "client_id" not in df.columns:
        raise ValueError(
            "File must contain a 'client_id' column (or a recognised alias such as 'id')."
        )

    added = updated = errors = 0
    for _, row in df.iterrows():
        try:
            result = upsert_client(org_id, row.to_dict())
            if result == "added":
                added += 1
            else:
                updated += 1
        except Exception as exc:
            logger.warning("Skipping row (client_id=%r): %s", row.get("client_id"), exc)
            errors += 1

    db.session.commit()
    return added, updated, errors
