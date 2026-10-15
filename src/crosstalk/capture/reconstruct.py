import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa

from crosstalk.capture.packet import TCPPacket, parse_tcp
from crosstalk.capture.pcap import read_capture
from crosstalk.capture.tcp import TCPStream
from crosstalk.capture.tls import TLSRecord, classify_records, parse_records
from crosstalk.common.artifacts import creation_mode
from crosstalk.common.errors import ReconstructionError, ValidationError
from crosstalk.contracts.parquet import BatchTableWriter, read_table, write_table
from crosstalk.contracts.tables import schema_for


@dataclass(slots=True)
class Flow:
    flow_id: int
    client_port: int
    server_port: int
    t_syn_ns: int
    t_last_ns: int
    client: TCPStream = field(default_factory=TCPStream)
    server: TCPStream = field(default_factory=TCPStream)
    packets_c2s: int = 0
    packets_s2c: int = 0
    max_ip_len: int = 0
    anomalies: list[str] = field(default_factory=lambda: [])
    records_c2s: list[TLSRecord] = field(default_factory=lambda: [])
    records_s2c: list[TLSRecord] = field(default_factory=lambda: [])


def _flow_key(packet: TCPPacket) -> tuple[tuple[str, int], tuple[str, int]]:
    if packet.destination_port == 8443:
        return ((packet.source, packet.source_port), (packet.destination, packet.destination_port))
    return ((packet.destination, packet.destination_port), (packet.source, packet.source_port))


def _write_table(path: Path, name: str, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValidationError(f"Artifact already exists: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    try:
        write_table(pa.Table.from_pylist(rows, schema=schema_for(name)), temporary)
        if path.exists():
            raise ValidationError(f"Artifact already exists: {path}")
        os.chmod(temporary, creation_mode())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def reconstruct(capture: Path, output: Path, session_id: str) -> dict[str, int]:
    flows: dict[tuple[tuple[str, int], tuple[str, int]], Flow] = {}
    with BatchTableWriter(output / "packets.parquet", schema_for("packets")) as packet_writer:
        for frame in read_capture(capture):
            packet = parse_tcp(frame)
            if packet is None or (packet.source_port != 8443 and packet.destination_port != 8443):
                continue
            key = _flow_key(packet)
            if key not in flows:
                flows[key] = Flow(
                    len(flows), key[0][1], key[1][1], frame.timestamp_ns, frame.timestamp_ns
                )
            flow = flows[key]
            direction = -1 if packet.destination_port == 8443 else 1
            stream = flow.client if direction == -1 else flow.server
            is_retrans, is_ooo = stream.add(packet)
            flow.t_last_ns = frame.timestamp_ns
            flow.max_ip_len = max(flow.max_ip_len, packet.ip_length)
            if direction == -1:
                flow.packets_c2s += 1
                if packet.flags & 2:
                    flow.t_syn_ns = frame.timestamp_ns
            else:
                flow.packets_s2c += 1
            rel_seq = (
                (packet.sequence - stream.initial_sequence) & 0xFFFFFFFF
                if stream.initial_sequence is not None
                else 0
            )
            packet_writer.add(
                {
                    "session_id": session_id,
                    "flow_id": flow.flow_id,
                    "dir": direction,
                    "ts_ns": frame.timestamp_ns,
                    "ip_len": packet.ip_length,
                    "tcp_len": len(packet.payload),
                    "flags": packet.flags,
                    "rel_seq": rel_seq,
                    "is_retrans": is_retrans,
                    "is_ooo": is_ooo,
                }
            )
    record_rows: list[dict[str, object]] = []
    flow_rows: list[dict[str, object]] = []
    for flow in flows.values():
        for direction, stream in ((-1, flow.client), (1, flow.server)):
            if stream.missing_syn or stream.initial_sequence is None:
                flow.anomalies.append(f"missing_syn:{direction}")
            if stream.pending:
                flow.anomalies.append(f"tcp_hole:{direction}")
            if stream.overlap_conflict:
                flow.anomalies.append(f"overlap_conflict:{direction}")
            if not stream.ended:
                flow.anomalies.append(f"incomplete:{direction}")
            if stream.ended and not stream.complete:
                flow.anomalies.append(f"incomplete_termination:{direction}")
            try:
                records = parse_records(stream)
            except ReconstructionError:
                flow.anomalies.append(f"tls_desync:{direction}")
                records = []
            if direction == -1:
                flow.records_c2s = records
            else:
                flow.records_s2c = records
        client_labels, server_labels = classify_records(flow.records_c2s, flow.records_s2c)
        for direction, records, labels in (
            (-1, flow.records_c2s, client_labels),
            (1, flow.records_s2c, server_labels),
        ):
            for record, phase in zip(records, labels, strict=True):
                record_rows.append(
                    {
                        "session_id": session_id,
                        "flow_id": flow.flow_id,
                        "dir": direction,
                        "idx": record.index,
                        "t_start_ns": record.start_ns,
                        "t_end_ns": record.end_ns,
                        "content_type": record.content_type,
                        "length": record.length,
                        "stream_offset": record.stream_offset,
                        "phase": phase,
                    }
                )
        flow_rows.append(
            {
                "session_id": session_id,
                "flow_id": flow.flow_id,
                "client_port": flow.client_port,
                "server_port": flow.server_port,
                "t_syn_ns": flow.t_syn_ns,
                "t_last_ns": flow.t_last_ns,
                "bytes_c2s": len(flow.client.data),
                "bytes_s2c": len(flow.server.data),
                "packets_c2s": flow.packets_c2s,
                "packets_s2c": flow.packets_s2c,
                "records_c2s": len(flow.records_c2s),
                "records_s2c": len(flow.records_s2c),
                "retrans_bytes_c2s": flow.client.retransmitted_bytes,
                "retrans_bytes_s2c": flow.server.retransmitted_bytes,
                "max_ip_len": flow.max_ip_len,
                "cipher_suite": None,
                "complete": not flow.anomalies,
                "anomalies": flow.anomalies,
            }
        )
    _write_table(output / "records.parquet", "records", record_rows)
    _write_table(output / "flows.parquet", "flows", flow_rows)
    return {
        "packets": packet_writer.count,
        "records": len(record_rows),
        "flows": len(flow_rows),
        "incomplete_flows": sum(bool(flow.anomalies) for flow in flows.values()),
    }


def map_flows(root: Path, session_id: str) -> int:
    observer = root / "sessions" / session_id / "observer"
    hints = read_table(observer / "flow_hints.parquet").to_pylist()
    flows = read_table(observer / "flows.parquet").to_pylist()
    by_port: dict[int, list[str]] = defaultdict(list)
    for hint in sorted(hints, key=lambda row: row["t_connect_ns"]):
        by_port[hint["client_port"]].append(hint["request_id"])
    result: list[dict[str, object]] = []
    for flow in sorted(flows, key=lambda row: row["t_syn_ns"]):
        candidates = by_port[flow["client_port"]]
        if not candidates:
            raise ValidationError(f"No flow hint for client port {flow['client_port']}")
        result.append(
            {"session_id": session_id, "flow_id": flow["flow_id"], "request_id": candidates.pop(0)}
        )
    if any(by_port.values()):
        raise ValidationError("Unmatched flow hints")
    _write_table(observer / "flow_map.parquet", "flow_map", result)
    return len(result)
