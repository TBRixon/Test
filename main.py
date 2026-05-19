"""
main.py
Entry point for the MyCRM → Cotality AVM → Refi Opportunity pipeline.

Usage:
    python main.py --input clients.csv [--output refi_opportunities_YYYYMMDD.xlsx]

The script:
  1. Reads a MyCRM CSV export
  2. Fetches AVM values from Cotality for each unique address
  3. Runs business logic (LVR, flags, scoring) on every row
  4. Writes a multi-sheet Excel workbook:
       - Sheet "Master"   – all clients
       - Sheet <broker>   – one sheet per broker, sorted by priority
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from avm_client import AVMClient
from config import MASTER_COLUMNS, OUTPUT_DATE_FORMAT
from processor import process_row
from utils import setup_logging

# Load .env before any config values are consumed
load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {
    "client_id",
    "client_name",
    "address",
    "loan_balance",
    "current_rate",
    "broker",
    "fixed_expiry",
}


def read_csv(path: Path) -> pd.DataFrame:
    """Load and basic-validate the MyCRM CSV export."""
    logger.info("Reading CSV: %s", path)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except FileNotFoundError:
        logger.error("Input file not found: %s", path)
        sys.exit(1)
    except Exception as exc:
        logger.error("Failed to read CSV: %s", exc)
        sys.exit(1)

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        logger.error("CSV is missing required columns: %s", sorted(missing))
        sys.exit(1)

    logger.info("Loaded %d rows from CSV.", len(df))
    return df


def enrich_dataframe(df: pd.DataFrame, client: AVMClient) -> list[dict]:
    """
    Iterate every CSV row, fetch AVM, and build the enriched output rows.
    Returns a list of dicts ready for DataFrame construction.
    """
    total     = len(df)
    success   = 0
    avm_fails = 0
    results   = []

    for idx, row in df.iterrows():
        address = str(row.get("address", "")).strip()
        logger.info("Processing row %d/%d – %s", idx + 1, total, address)

        avm = client.get_avm(address)
        if avm is None:
            avm_fails += 1
            logger.warning("AVM unavailable for row %d (%s).", idx + 1, address)
        else:
            success += 1

        results.append(process_row(row.to_dict(), avm))

    logger.info(
        "Processing complete – total: %d | AVM success: %d | AVM failures: %d",
        total,
        success,
        avm_fails,
    )
    return results


def build_master_df(rows: list[dict]) -> pd.DataFrame:
    """Construct the Master sheet DataFrame, selecting and ordering columns."""
    df = pd.DataFrame(rows)
    # Drop internal helper columns
    df = df.drop(columns=[c for c in df.columns if c.startswith("_")], errors="ignore")
    # Ensure all expected columns exist (fill missing with empty string)
    for col in MASTER_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[MASTER_COLUMNS]


def write_excel(master_df: pd.DataFrame, rows: list[dict], output_path: Path) -> None:
    """Write the master sheet and per-broker sheets to an Excel workbook."""
    # Working copy with Priority Score retained for sorting
    full_df = pd.DataFrame(rows).drop(
        columns=[c for c in pd.DataFrame(rows).columns if c.startswith("_")],
        errors="ignore",
    )

    broker_sort_cols = ["Priority Score", "LVR"]
    broker_sort_asc  = [False, True]   # highest score first, then lowest LVR

    logger.info("Writing Excel workbook: %s", output_path)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # --- Master sheet -----------------------------------------------
        master_df.to_excel(writer, sheet_name="Master", index=False)
        _format_sheet(writer, "Master", master_df)

        # --- Broker sheets ----------------------------------------------
        brokers = sorted(full_df["Broker"].dropna().unique())
        for broker in brokers:
            sheet_name = _safe_sheet_name(broker)
            broker_df  = full_df[full_df["Broker"] == broker].copy()

            # Sort by priority: highest score first, then lowest LVR
            sort_cols_present = [c for c in broker_sort_cols if c in broker_df.columns]
            if sort_cols_present:
                broker_df = broker_df.sort_values(
                    by=sort_cols_present,
                    ascending=broker_sort_asc[: len(sort_cols_present)],
                )

            # Select only Master columns that are present
            export_cols = [c for c in MASTER_COLUMNS if c in broker_df.columns]
            broker_df   = broker_df[export_cols]

            broker_df.to_excel(writer, sheet_name=sheet_name, index=False)
            _format_sheet(writer, sheet_name, broker_df)

    logger.info("Workbook saved – sheets: Master + %d broker sheet(s).", len(brokers))


def _format_sheet(writer: pd.ExcelWriter, sheet_name: str, df: pd.DataFrame) -> None:
    """Apply basic formatting: auto-width columns, freeze top row."""
    ws = writer.sheets[sheet_name]

    # Freeze header row
    ws.freeze_panes = "A2"

    # Auto-fit column widths
    for col_idx, col_name in enumerate(df.columns, start=1):
        max_len = max(
            len(str(col_name)),
            df[col_name].astype(str).str.len().max() if len(df) else 0,
        )
        # Clamp between 10 and 60 characters
        ws.column_dimensions[
            ws.cell(row=1, column=col_idx).column_letter
        ].width = min(max(max_len + 2, 10), 60)


def _safe_sheet_name(name: str) -> str:
    """Excel sheet names must be ≤ 31 chars and exclude certain characters."""
    invalid = r"\/:*?[]"
    clean   = "".join(c if c not in invalid else "_" for c in str(name))
    return clean[:31]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MyCRM → Cotality AVM → Refi Opportunity report generator."
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="CSV_FILE",
        help="Path to the MyCRM CSV export.",
    )
    parser.add_argument(
        "--output",
        metavar="XLSX_FILE",
        default=f"refi_opportunities_{date.today().strftime(OUTPUT_DATE_FORMAT)}.xlsx",
        help="Output Excel file path (default: refi_opportunities_YYYYMMDD.xlsx).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(getattr(logging, args.log_level))

    input_path  = Path(args.input)
    output_path = Path(args.output)

    # 1. Read CSV
    raw_df = read_csv(input_path)

    # 2. Initialise AVM client (validates env vars once)
    avm_client = AVMClient()

    # 3. Enrich each row
    enriched_rows = enrich_dataframe(raw_df, avm_client)

    if not enriched_rows:
        logger.error("No rows to process. Exiting.")
        sys.exit(1)

    # 4. Build master DataFrame
    master_df = build_master_df(enriched_rows)

    # 5. Write Excel
    write_excel(master_df, enriched_rows, output_path)

    logger.info("Done. Output: %s", output_path.resolve())


if __name__ == "__main__":
    main()
