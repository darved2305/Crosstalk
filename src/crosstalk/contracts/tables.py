from collections.abc import Mapping

import pyarrow as pa

I8 = pa.int8()
I32 = pa.int32()
I64 = pa.int64()
U8 = pa.uint8()
U16 = pa.uint16()
U32 = pa.uint32()
S = pa.string()
B = pa.bool_()
F32 = pa.float32()


def _schema(fields: list[tuple[str, pa.DataType]]) -> pa.Schema:
    return pa.schema([pa.field("session_id", S), *(pa.field(name, kind) for name, kind in fields)])


SCHEMAS: Mapping[str, pa.Schema] = {
    "packets": _schema(
        [
            ("flow_id", I32),
            ("dir", I8),
            ("ts_ns", I64),
            ("ip_len", U16),
            ("tcp_len", U16),
            ("flags", U8),
            ("rel_seq", U32),
            ("is_retrans", B),
            ("is_ooo", B),
        ]
    ),
    "records": _schema(
        [
            ("flow_id", I32),
            ("dir", I8),
            ("idx", I32),
            ("t_start_ns", I64),
            ("t_end_ns", I64),
            ("content_type", U8),
            ("length", U16),
            ("stream_offset", I64),
            ("phase", S),
        ]
    ),
    "flows": _schema(
        [
            ("flow_id", I32),
            ("client_port", U16),
            ("server_port", U16),
            ("t_syn_ns", I64),
            ("t_last_ns", I64),
            ("bytes_c2s", I64),
            ("bytes_s2c", I64),
            ("packets_c2s", I32),
            ("packets_s2c", I32),
            ("records_c2s", I32),
            ("records_s2c", I32),
            ("retrans_bytes_c2s", I64),
            ("retrans_bytes_s2c", I64),
            ("max_ip_len", U16),
            ("cipher_suite", U16),
            ("complete", B),
            ("anomalies", pa.list_(S)),
        ]
    ),
    "flow_hints": _schema([("request_id", S), ("client_port", U16), ("t_connect_ns", I64)]),
    "flow_map": _schema([("flow_id", I32), ("request_id", S)]),
    "emissions": _schema(
        [
            ("request_id", S),
            ("seq", I32),
            ("t_emit_ns", I64),
            ("kind", S),
            ("framed_len", I32),
            ("content_bytes", I32),
            ("pad_bytes", I32),
            ("n_events", I32),
            ("queue_delay_ns", I64),
            ("timer_lateness_ns", I64),
            ("arm", S),
        ]
    ),
    "requests": _schema(
        [
            ("request_id", S),
            ("slot", I32),
            ("arm", S),
            ("stream_interval", I32),
            ("client_port", U16),
            ("t_connect_ns", I64),
            ("t_tls_ns", I64),
            ("t_sent_ns", I64),
            ("t_first_byte_ns", I64),
            ("t_first_content_ns", I64),
            ("t_done_ns", I64),
            ("status", S),
            ("error_code", S),
            ("prompt_tokens", I32),
            ("completion_tokens", I32),
        ]
    ),
    "client_events": _schema(
        [
            ("request_id", S),
            ("idx", I32),
            ("t_recv_ns", I64),
            ("is_content", B),
            ("framed_len", I32),
        ]
    ),
    "client_reads": _schema(
        [
            ("request_id", S),
            ("idx", I32),
            ("t_recv_ns", I64),
            ("plaintext_len", I32),
        ]
    ),
    "labels": _schema(
        [
            ("request_id", S),
            ("prompt_id", S),
            ("class", S),
            ("family_id", S),
            ("template_id", S),
            ("regime", S),
            ("prompt_bucket", I32),
            ("target_len", I32),
            ("t2_sensitive", B),
        ]
    ),
    "text": _schema([("request_id", S), ("prompt_text", S), ("response_text", S)]),
    "features": _schema(
        [("request_id", S), ("arm", S), ("feature_names", pa.list_(S)), ("values", pa.list_(F32))]
    ),
    "predictions": _schema(
        [
            ("request_id", S),
            ("fold", I32),
            ("arm", S),
            ("setting", S),
            ("model", S),
            ("seed", I32),
            ("y_true", S),
            ("proba", pa.list_(F32)),
        ]
    ),
    "evaluations": _schema(
        [
            ("experiment_id", S),
            ("arm", S),
            ("setting", S),
            ("model", S),
            ("metric", S),
            ("value", F32),
            ("ci_lo", F32),
            ("ci_hi", F32),
            ("n", I32),
            ("n_families", I32),
            ("B", I32),
        ]
    ),
}

OBSERVER_TABLES = frozenset({"packets", "records", "flows", "flow_hints", "flow_map"})
ORACLE_TABLES = frozenset({"emissions", "requests", "client_events", "client_reads"})


def schema_for(name: str) -> pa.Schema:
    return SCHEMAS[name]
