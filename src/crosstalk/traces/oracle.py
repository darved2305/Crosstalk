from pathlib import Path

import pyarrow as pa

from crosstalk.common.errors import OracleAccessError
from crosstalk.common.ids import is_ulid
from crosstalk.contracts.parquet import read_table
from crosstalk.contracts.tables import ORACLE_TABLES, schema_for


def load(root: Path, session_id: str, table: str) -> pa.Table:
    if table not in ORACLE_TABLES:
        raise OracleAccessError(f"Oracle access denied: {table}")
    if not is_ulid(session_id):
        raise OracleAccessError("Invalid session ID")
    base = root.resolve() / "sessions" / session_id / "oracle"
    if base.resolve() != base:
        raise OracleAccessError("Oracle directory redirects outside its expected path")
    target = base / f"{table}.parquet"
    if not target.resolve().is_relative_to(base):
        raise OracleAccessError("Oracle path escapes session")
    result = read_table(target)
    if result.schema != schema_for(table):
        raise OracleAccessError(f"Unexpected oracle schema: {table}")
    return result
