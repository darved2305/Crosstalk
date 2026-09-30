import json
from pathlib import Path

import pytest

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
