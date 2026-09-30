from pathlib import Path

import pyarrow as pa

from crosstalk.common.errors import OracleAccessError
from crosstalk.common.ids import is_ulid
from crosstalk.contracts.parquet import read_table
from crosstalk.contracts.tables import OBSERVER_TABLES, schema_for


def load(root: Path, session_id: str, table: str) -> pa.Table:
    if table not in OBSERVER_TABLES:
        raise OracleAccessError(f"Observer access denied: {table}")
    if not is_ulid(session_id):
        raise OracleAccessError("Invalid session ID")
    parent = root.resolve() / "sessions"
    base = parent / session_id / "observer"
    if base.resolve() != base:
        raise OracleAccessError("Observer directory redirects outside its expected path")
    target = base / f"{table}.parquet"
    if target.resolve().is_relative_to(base):
        result = read_table(target)
        if result.schema != schema_for(table):
            raise OracleAccessError(f"Unexpected observer schema: {table}")
        return result
    raise OracleAccessError("Observer path escapes session")


def load_labels(root: Path, session_id: str) -> pa.Table:
    if not is_ulid(session_id):
        raise OracleAccessError("Invalid session ID")
    base = root.resolve() / "labels" / session_id
    if base.resolve() != base:
        raise OracleAccessError("Labels directory redirects outside its expected path")
    target = base / "labels.parquet"
    if not target.resolve().is_relative_to(base):
        raise OracleAccessError("Labels path escapes session")
    result = read_table(target)
    if result.schema != schema_for("labels"):
        raise OracleAccessError("Unexpected labels schema")
    return result
