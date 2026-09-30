from pathlib import Path

import pytest

from crosstalk.capture.control import CaptureSupervisor
from crosstalk.common.errors import CaptureError
from crosstalk.common.ids import new_ulid


@pytest.mark.asyncio
async def test_capture_control_rejects_broad_filter(tmp_path: Path) -> None:
    supervisor = CaptureSupervisor(tmp_path, "eth0")
    with pytest.raises(CaptureError, match="filter"):
        await supervisor.start(new_ulid(), bpf="tcp")
    with pytest.raises(CaptureError, match="ring"):
        await supervisor.start(new_ulid(), ring_mb=5000)
