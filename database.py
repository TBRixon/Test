"""
database.py
SQLite storage layer for client records.

Responsibilities:
  - Schema creation and migration
  - Upsert logic (insert on new client_id, update on duplicate)
  - Column name normalisation for flexible file uploads
  - CSV/Excel file ingestion
  - Seed from sample CSV on first run
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "refi_pipeline.db"

# ---------------------------------------------------------------------------
# Canonical column aliases – maps any recognised variant to the DB column name
# ---------------------------------------------------------------------------
_COLUMN_ALIASES: dict[str, str] = {
    # client_id
    "id":             "client_id",
    "client_id":      "client_id",
    "clientid":       "client_id",
    "customer_id":    "client_id",
    # client_name
    "name":           "client_name",
    "client_name":    "client_name",
    "full_name":      "client_name",
    "customer_name":  "client_name",
    # address
    "address":        "address",
    "property_address": "address",
    "prop_address":   "address",
    # loan_balance
    "loan":           "loan_balance",
    "balance":        "loan_balance",
    "loan_balance":   "loan_balance",
    "loan_amount":    "loan_balance",
    "outstanding_balance": "loan_balance",
    # current_rate
    "rate":           "current_rate",
    "interest_rate":  "current_rate",
    "current_rate":   "current_rate",
    "ir":             "current_rate",
    # broker
    "broker":         "broker",
    "broker_name":    "broker",
    "adviser":        "broker",
    "advisor":        "broker",
    # fixed_expiry
    "expiry":         "fixed_expiry",
    "fixed_expiry":   "fixed_expiry",
    "fixed_rate_expiry": "fixed_expiry",
    "expiry_date":    "fixed_expiry",
    "rate_expiry":    "fixed_expiry",
}

CANONICAL_COLUMNS = [
    "client_id", "client_name", "address",
    "loan_balance", "current_rate", "broker", "fixed_expiry",
]


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                client_id    TEXT PRIMARY KEY,
                client_name  TEXT DEFAULT '',
                address      TEXT DEFAULT '',
                loan_balance TEXT DEFAULT '',
                current_rate TEXT DEFAULT '',
                broker       TEXT DEFAULT '',
                fixed_expiry TEXT DEFAULT '',
                created_at   TEXT DEFAULT (datetime('now')),
                updated_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    logger.info("Database initialised: %s", DB_PATH)


# ---------------------------------------------------------------------------
# Column normalisation
# ---------------------------------------------------------------------------

def normalise_columns(columns: list[str]) -> dict[str, str]:
    """
    Return a rename mapping from incoming column names to canonical DB names.
    Unrecognised columns are omitted from the result (they will be ignored).
    """
    mapping: dict[str, str] = {}
    for col in columns:
        key = col.strip().lower().replace(" ", "_").replace("-", "_")
        canonical = _COLUMN_ALIASES.get(key)
        if canonical:
            mapping[col] = canonical
    return mapping


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_client(conn: sqlite3.Connection, record: dict) -> str:
    """
    Insert or update a single client record.
    Returns 'added' for a new row, 'updated' for an existing one.
    The record dict must contain 'client_id'; other fields default to ''.
    """
    cid = str(record.get("client_id", "")).strip()
    if not cid:
        raise ValueError("Record is missing a non-empty client_id.")

    exists = conn.execute(
        "SELECT 1 FROM clients WHERE client_id = ?", (cid,)
    ).fetchone()

    fields = {col: str(record.get(col, "") or "") for col in CANONICAL_COLUMNS}

    if exists:
        conn.execute("""
            UPDATE clients SET
                client_name  = ?,
                address      = ?,
                loan_balance = ?,
                current_rate = ?,
                broker       = ?,
                fixed_expiry = ?,
                updated_at   = datetime('now')
            WHERE client_id = ?
        """, (
            fields["client_name"], fields["address"],
            fields["loan_balance"], fields["current_rate"],
            fields["broker"], fields["fixed_expiry"],
            cid,
        ))
        return "updated"
    else:
        conn.execute("""
            INSERT INTO clients
                (client_id, client_name, address, loan_balance, current_rate, broker, fixed_expiry)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            cid, fields["client_name"], fields["address"],
            fields["loan_balance"], fields["current_rate"],
            fields["broker"], fields["fixed_expiry"],
        ))
        return "added"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_all_clients() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM clients ORDER BY client_id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_client_count() -> int:
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]


# ---------------------------------------------------------------------------
# Seed from CSV
# ---------------------------------------------------------------------------

def seed_from_csv(csv_path: Path) -> None:
    """Populate the DB from sample_data.csv only if the table is currently empty."""
    if get_client_count() > 0:
        return

    logger.info("Empty database – seeding from %s", csv_path)
    try:
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    except Exception as exc:
        logger.error("Could not read seed CSV: %s", exc)
        return

    added, _updated, errors = _ingest_dataframe(df)
    logger.info("Seed complete – %d rows added, %d errors.", added, errors)


# ---------------------------------------------------------------------------
# Upload processing
# ---------------------------------------------------------------------------

def process_upload(file_storage) -> dict:
    """
    Accept a Flask FileStorage object (CSV or Excel), parse it, upsert every
    row, and return {"added", "updated", "errors", "total"}.
    Raises ValueError with a user-friendly message on structural problems.
    """
    filename = (file_storage.filename or "").lower()

    if filename.endswith(".csv"):
        try:
            df = pd.read_csv(file_storage, dtype=str, keep_default_na=False)
        except Exception as exc:
            raise ValueError(f"Could not parse CSV file: {exc}") from exc
    elif filename.endswith((".xlsx", ".xls")):
        try:
            df = pd.read_excel(file_storage, dtype=str)
            df = df.fillna("")
        except Exception as exc:
            raise ValueError(f"Could not parse Excel file: {exc}") from exc
    else:
        raise ValueError("Unsupported file type. Please upload a .csv or .xlsx file.")

    added, updated, errors = _ingest_dataframe(df)
    total = added + updated
    logger.info("Upload complete – added: %d, updated: %d, errors: %d", added, updated, errors)
    return {"added": added, "updated": updated, "errors": errors, "total": total}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ingest_dataframe(df: pd.DataFrame) -> tuple[int, int, int]:
    """
    Normalise columns, validate, and upsert all rows.
    Returns (added, updated, errors).
    """
    df.columns = [str(c) for c in df.columns]
    rename_map = normalise_columns(list(df.columns))
    df = df.rename(columns=rename_map)

    if "client_id" not in df.columns:
        raise ValueError(
            "File must contain a 'client_id' column (or a recognised alias such as 'id')."
        )

    added = updated = errors = 0

    with get_connection() as conn:
        for _, row in df.iterrows():
            try:
                result = upsert_client(conn, row.to_dict())
                if result == "added":
                    added += 1
                else:
                    updated += 1
            except Exception as exc:
                logger.warning("Skipping row (client_id=%r): %s", row.get("client_id"), exc)
                errors += 1
        conn.commit()

    return added, updated, errors
