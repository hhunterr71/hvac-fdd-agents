"""Load a Trend File: a wide CSV with a timestamp column first, then one column per point."""

import csv
from datetime import datetime
from pathlib import Path

import duckdb

TIMESTAMP_FORMATS = ("%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S")


def detect_timestamp_format(value: str) -> str:
    for fmt in TIMESTAMP_FORMATS:
        try:
            datetime.strptime(value.strip(), fmt)
            return fmt
        except ValueError:
            continue
    raise ValueError(f"first column doesn't look like a timestamp: {value!r}")


def read_header(trend: Path) -> tuple[str, list[str], str]:
    """Return the timestamp column name, the point names, and the timestamp format."""
    with Path(trend).open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        first_row = next(reader)
    return header[0], header[1:], detect_timestamp_format(first_row[0])


def quote(name: str) -> str:
    """Quote a column name for SQL (point names can contain %, -, and spaces)."""
    return '"' + name.replace('"', '""') + '"'


def load(trend: Path, con: duckdb.DuckDBPyConnection | None = None) -> tuple[duckdb.DuckDBPyConnection, list[str]]:
    """Load a Trend File into table `trend` with a TIMESTAMP column `ts`. Returns (connection, points)."""
    con = con or duckdb.connect()
    ts_col, points, fmt = read_header(trend)
    path = str(Path(trend)).replace("'", "''")
    ts_literal = "'" + ts_col.replace("'", "''") + "'"
    con.execute(
        f"create or replace table trend as "
        f"select strptime({quote(ts_col)}, '{fmt}') as ts, * exclude ({quote(ts_col)}) "
        f"from read_csv('{path}', types={{{ts_literal}: 'VARCHAR'}}) "
        f"order by ts"
    )
    return con, points
