import asyncio
import json
from pathlib import Path

import pytest

from crosstalk.contracts.parquet import read_table
from crosstalk.loadgen.session import _collect_emissions
from crosstalk.shaper.accounting import Emission, EmissionWriter


@pytest.mark.asyncio
async def test_emission_writer_reports_open_failure(tmp_path: Path) -> None:
    path = tmp_path / "emissions.ndjson"
    path.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        async with EmissionWriter(path):
            pass


@pytest.mark.asyncio
async def test_emission_writer_flushes_rows(tmp_path: Path) -> None:
    path = tmp_path / "emissions.ndjson"
    row = Emission("request", 0, 100, "content", 24, 20, 0, 1, 0, 0, "a0")
    async with EmissionWriter(path) as writer:
        await writer.record(row)
    assert json.loads(path.read_text(encoding="utf-8"))["framed_len"] == 24


@pytest.mark.asyncio
async def test_collection_waits_for_queued_terminal_emission(tmp_path: Path) -> None:
    session_id = "01K00000000000000000000001"
    request_id = "01K00000000000000000000002"
    path = tmp_path / "shaper-log" / "emissions.ndjson"
    async with EmissionWriter(path) as writer:

        async def delayed_terminal() -> None:
            await writer.record(Emission(request_id, 0, 100, "headers", 24, 0, 0, 0, 0, 0, "a0"))
            await asyncio.sleep(0.1)
            await writer.record(Emission(request_id, 1, 200, "terminator", 5, 0, 0, 0, 0, 0, "a0"))

        task = asyncio.create_task(delayed_terminal())
        await _collect_emissions(tmp_path, session_id, {request_id}, {request_id})
        await task
    rows = read_table(
        tmp_path / "sessions" / session_id / "oracle" / "emissions.parquet"
    ).to_pylist()
    assert [row["kind"] for row in rows] == ["headers", "terminator"]
