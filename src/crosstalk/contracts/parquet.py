import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import pyarrow as pa
import pyarrow.parquet as pq

from crosstalk.common.errors import ValidationError


class _Writer(Protocol):
    def write_table(self, table: pa.Table) -> None: ...
    def close(self) -> None: ...


def read_table(path: Path) -> pa.Table:
    reader = cast(Callable[[Path], pa.Table], vars(pq)["read_table"])
    return reader(path)


def write_table(table: pa.Table, path: Path | str) -> None:
    writer = cast(Callable[[pa.Table, Path | str], None], vars(pq)["write_table"])
    writer(table, path)


class BatchTableWriter:
    def __init__(self, path: Path, schema: pa.Schema, batch_size: int = 8192) -> None:
        self.path = path
        self.schema = schema
        self.batch_size = batch_size
        self.rows: list[dict[str, object]] = []
        self.writer: _Writer | None = None
        self.temporary: str | None = None
        self.count = 0

    def __enter__(self) -> "BatchTableWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise ValidationError(f"Artifact already exists: {self.path}")
        descriptor, self.temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        os.close(descriptor)
        constructor = cast(Callable[[str, pa.Schema], _Writer], vars(pq)["ParquetWriter"])
        self.writer = constructor(self.temporary, self.schema)
        return self

    def add(self, row: dict[str, object]) -> None:
        self.rows.append(row)
        self.count += 1
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if self.writer is None:
            raise RuntimeError("Parquet writer is not open")
        if self.rows:
            self.writer.write_table(pa.Table.from_pylist(self.rows, schema=self.schema))
            self.rows.clear()

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        try:
            if self.writer is not None:
                if error_type is None:
                    self.flush()
                self.writer.close()
            if error_type is None and self.temporary is not None:
                if self.path.exists():
                    raise ValidationError(f"Artifact already exists: {self.path}")
                os.replace(self.temporary, self.path)
        finally:
            if self.temporary is not None and os.path.exists(self.temporary):
                os.unlink(self.temporary)
