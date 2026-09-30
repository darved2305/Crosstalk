import json
import subprocess
from collections import defaultdict
from pathlib import Path

from crosstalk.common.artifacts import atomic_replace, atomic_write
from crosstalk.common.errors import ValidationError
from crosstalk.contracts.parquet import read_table
from crosstalk.shaper.sse import SSEParser


def validate_keylogs(root: Path, session_id: str, capture: Path) -> dict[str, object] | None:
    restricted = (root / "restricted" / session_id).resolve()
    keylog_directory = restricted / "keylogs"
    files = sorted(
        path for path in keylog_directory.glob("*.keylog") if path.name != "combined.keylog"
    )
    if not files:
        return None
    if any(not path.resolve().is_relative_to(restricted) for path in files):
        raise ValidationError("Keylog path escapes restricted tree")
    combined = keylog_directory / "combined.keylog"
    combined_data = b"\n".join(path.read_bytes() for path in files) + b"\n"
    if combined.exists():
        atomic_replace(combined, combined_data)
    else:
        atomic_write(combined, combined_data)
    session = root / "sessions" / session_id
    flow_map = read_table(session / "observer" / "flow_map.parquet").to_pylist()
    flows = read_table(session / "observer" / "flows.parquet").to_pylist()
    text = read_table(restricted / "text.parquet").to_pylist()
    port_by_flow = {row["flow_id"]: row["client_port"] for row in flows}
    request_by_port = {port_by_flow[row["flow_id"]]: row["request_id"] for row in flow_map}
    expected = {row["request_id"]: row["response_text"] for row in text}
    command = [
        "tshark",
        "-r",
        str(capture),
        "-o",
        f"tls.keylog_file:{combined}",
        "-o",
        "tcp.desegment_tcp_streams:TRUE",
        "-o",
        "http.desegment_body:TRUE",
        "-Y",
        "http.file_data && tcp.srcport==8443",
        "-T",
        "jsonraw",
    ]
    process = subprocess.run(command, capture_output=True, text=True, check=True)
    data_by_request: dict[str, bytearray] = defaultdict(bytearray)
    for packet in json.loads(process.stdout):
        layers = packet["_source"]["layers"]
        port = int(layers["tcp"]["tcp.dstport_raw"][0], 16)
        if port in request_by_port:
            data_by_request[request_by_port[port]].extend(
                bytes.fromhex(layers["http"]["http.file_data_raw"][0])
            )
    checked = 0
    mismatches: list[str] = []
    for request_id, plaintext in data_by_request.items():
        parser = SSEParser()
        content: list[str] = []
        for event in parser.feed(bytes(plaintext)):
            if event.data != "[DONE]":
                payload = json.loads(event.data)
                content.extend(
                    choice.get("delta", {}).get("content") or ""
                    for choice in payload.get("choices", [])
                )
        checked += 1
        if "".join(content) != expected[request_id]:
            mismatches.append(request_id)
    mismatches.extend(sorted(set(expected) - set(data_by_request)))
    result: dict[str, object] = {
        "exact": not mismatches,
        "checked": checked,
        "mismatches": mismatches,
    }
    atomic_write(
        session / "validation" / "keylog_check.json", json.dumps(result, indent=2).encode()
    )
    return result
