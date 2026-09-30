import json
import subprocess
from collections import defaultdict
from pathlib import Path

from crosstalk.common.artifacts import atomic_write
from crosstalk.common.errors import ValidationError
from crosstalk.contracts.parquet import read_table


def validate_tshark(root: Path, session_id: str, capture: Path) -> dict[str, object]:
    observer = root / "sessions" / session_id / "observer"
    flows = read_table(observer / "flows.parquet").to_pylist()
    records = read_table(observer / "records.parquet").to_pylist()
    by_port = {row["client_port"]: row["flow_id"] for row in flows}
    if len(by_port) != len(flows):
        raise ValidationError("Client port reuse needs disambiguation before tshark comparison")
    command = [
        "tshark",
        "-r",
        str(capture),
        "-o",
        "tcp.desegment_tcp_streams:TRUE",
        "-o",
        "tls.desegment_ssl_records:TRUE",
        "-Y",
        "tcp.port==8443 && tls",
        "-T",
        "fields",
        "-E",
        "separator=/t",
        "-E",
        "occurrence=a",
        "-e",
        "frame.number",
        "-e",
        "frame.time_epoch",
        "-e",
        "tcp.srcport",
        "-e",
        "tcp.dstport",
        "-e",
        "tls.record.content_type",
        "-e",
        "tls.record.length",
    ]
    process = subprocess.run(command, capture_output=True, text=True, check=True)
    oracle: dict[tuple[int, int], list[tuple[int, int, int, int]]] = defaultdict(list)
    for line in process.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 6 or not parts[4] or not parts[5]:
            continue
        frame, epoch, source_port, destination_port, types, lengths = parts
        direction = 1 if int(source_port) == 8443 else -1
        client_port = int(destination_port if direction == 1 else source_port)
        if client_port not in by_port:
            continue
        for content_type, length in zip(types.split(","), lengths.split(","), strict=True):
            oracle[(by_port[client_port], direction)].append(
                (int(content_type), int(length), int(frame), round(float(epoch) * 1_000_000_000))
            )
    ours: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)
    for row in records:
        ours[(row["flow_id"], row["dir"])].append(
            (row["content_type"], row["length"], row["t_end_ns"])
        )
    mismatches: list[dict[str, object]] = []
    for key in sorted(set(ours) | set(oracle)):
        expected = oracle[key]
        actual = ours[key]
        if len(actual) != len(expected) or any(
            actual[index][:2] != item[:2] or abs(actual[index][2] - item[3]) > 1000
            for index, item in enumerate(expected[: len(actual)])
        ):
            mismatches.append(
                {"flow_id": key[0], "dir": key[1], "ours": actual, "tshark": expected}
            )
    result: dict[str, object] = {
        "exact": not mismatches,
        "ours": sum(map(len, ours.values())),
        "tshark": sum(map(len, oracle.values())),
        "mismatches": mismatches,
        "command": command,
    }
    target = root / "sessions" / session_id / "validation" / "tshark_agreement.json"
    atomic_write(target, json.dumps(result, indent=2).encode())
    return result


def validate_emissions(root: Path, session_id: str) -> dict[str, object]:
    session = root / "sessions" / session_id
    emissions = read_table(session / "oracle" / "emissions.parquet").to_pylist()
    records = read_table(session / "observer" / "records.parquet").to_pylist()
    mapping = read_table(session / "observer" / "flow_map.parquet").to_pylist()
    request_by_flow = {row["flow_id"]: row["request_id"] for row in mapping}
    expected: dict[str, list[int]] = defaultdict(list)
    actual: dict[str, list[int]] = defaultdict(list)
    for row in sorted(emissions, key=lambda item: (item["request_id"], item["seq"])):
        expected[row["request_id"]].append(row["framed_len"] + 17)
    for row in sorted(records, key=lambda item: (item["flow_id"], item["idx"])):
        if row["dir"] == 1 and row["phase"] == "response" and row["flow_id"] in request_by_flow:
            actual[request_by_flow[row["flow_id"]]].append(row["length"])
    mismatches: list[dict[str, object]] = []
    matches = 0
    total = 0
    for request_id in sorted(set(expected) | set(actual)):
        want = expected[request_id]
        got = actual[request_id]
        total += max(len(want), len(got))
        matches += sum(a == b for a, b in zip(want, got, strict=False))
        if want != got:
            mismatches.append({"request_id": request_id, "expected": want, "actual": got})
    result: dict[str, object] = {
        "exact_fraction": matches / total if total else 0.0,
        "matched": matches,
        "total": total,
        "mismatches": mismatches,
    }
    atomic_write(
        session / "validation" / "emission_fidelity.json", json.dumps(result, indent=2).encode()
    )
    return result


def evaluate_session(root: Path, session_id: str) -> dict[str, object]:
    session = root / "sessions" / session_id
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    capture = json.loads((session / "capture.json").read_text(encoding="utf-8"))
    flows = read_table(session / "observer" / "flows.parquet").to_pylist()
    tshark = json.loads(
        (session / "validation" / "tshark_agreement.json").read_text(encoding="utf-8")
    )
    fidelity = json.loads(
        (session / "validation" / "emission_fidelity.json").read_text(encoding="utf-8")
    )
    reasons: list[str] = []
    for key in ("kernel_drops", "interface_drops", "os_drops"):
        if capture[key] is None:
            reasons.append(f"{key} unknown")
        elif capture[key] != 0:
            reasons.append(f"{key} nonzero")
    if capture["exit_code"] != 0:
        reasons.append("dumpcap exited unsuccessfully")
    if capture.get("ring_full"):
        reasons.append("Capture ring reached capacity")
    offloads = capture["offloads"]
    for feature in (
        "generic-receive-offload",
        "large-receive-offload",
        "tcp-segmentation-offload",
        "generic-segmentation-offload",
    ):
        if f"{feature}: off" not in offloads:
            reasons.append(f"{feature} not verified off")
    if any(not row["complete"] for row in flows):
        reasons.append("Incomplete or anomalous TCP flow")
    if any(row["max_ip_len"] > capture["mtu"] for row in flows):
        reasons.append("IP packet exceeds measured MTU")
    if not tshark["exact"]:
        reasons.append("TLS records disagree with tshark")
    if fidelity["exact_fraction"] < 0.9999:
        reasons.append("Emission to record fidelity below 99.99%")
    keylog_result = session / "validation" / "keylog_check.json"
    if (
        keylog_result.exists()
        and not json.loads(keylog_result.read_text(encoding="utf-8"))["exact"]
    ):
        reasons.append("Restricted keylog plaintext comparison failed")
    if manifest["git_dirty"]:
        reasons.append("Source tree was dirty during collection")
    failure_rate = manifest["failed"] / manifest["planned"]
    if failure_rate > 0.02:
        reasons.append("More than 2% of requests failed")
    status = (
        "PARTIAL"
        if failure_rate > 0.02 and len(reasons) == 1
        else "INVALID"
        if reasons
        else "VALID"
    )
    result: dict[str, object] = {"status": status, "reasons": reasons}
    atomic_write(session / "validation" / "session.json", json.dumps(result, indent=2).encode())
    return result
