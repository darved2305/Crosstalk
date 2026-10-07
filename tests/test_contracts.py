import json
from pathlib import Path

import pyarrow as pa
import pytest
from pydantic import ValidationError as PydanticValidationError

from crosstalk.common.artifacts import atomic_write
from crosstalk.common.errors import CrosstalkError, OracleAccessError, ValidationError
from crosstalk.common.ids import is_ulid, new_ulid
from crosstalk.contracts.config import CaptureConfig, ExperimentConfig, SessionPlan
from crosstalk.contracts.manifest import RunManifest, SessionStatus
from crosstalk.contracts.parquet import BatchTableWriter, read_table
from crosstalk.contracts.tables import OBSERVER_TABLES, ORACLE_TABLES, SCHEMAS, schema_for
from crosstalk.traces.observer import load


def test_error_hierarchy() -> None:
    assert issubclass(OracleAccessError, CrosstalkError)
    assert issubclass(ValidationError, CrosstalkError)


def test_ulid_roundtrip() -> None:
    value = new_ulid()
    assert is_ulid(value)
    assert not is_ulid(value.lower())


def test_config_roundtrip_and_hash() -> None:
    config = ExperimentConfig(
        name="validation",
        sessions=[SessionPlan(experiment_id="validation", requests=200, concurrency=16, seed=3)],
    )
    assert ExperimentConfig.model_validate_json(config.model_dump_json()) == config
    assert (
        config.sha256() == ExperimentConfig.model_validate_json(config.model_dump_json()).sha256()
    )
    assert "sessions" in ExperimentConfig.model_json_schema()["properties"]
    with pytest.raises(PydanticValidationError):
        CaptureConfig(interface="eth0", bpf="")
    with pytest.raises(PydanticValidationError):
        SessionPlan(experiment_id="x", requests=0, concurrency=1, seed=0)


def test_manifest_roundtrip() -> None:
    manifest = RunManifest(
        session_id=new_ulid(),
        experiment_id="v",
        config_sha256="a" * 64,
        plan_sha256="b" * 64,
        git_commit="c" * 40,
        git_dirty=False,
        model_id="m",
        workload_version="v1",
        workload_sha256="d" * 64,
        policy_set_sha256="e" * 64,
        seeds={"plan": 1},
        stream_interval=1,
        concurrency=16,
        environment={},
        timing={},
        planned=200,
        status=SessionStatus.PARTIAL,
    )
    assert RunManifest.model_validate_json(manifest.model_dump_json()) == manifest
    assert json.loads(manifest.model_dump_json())["status"] == "PARTIAL"
    assert "source tree was dirty" not in manifest.reportability_issues()
    assert "session status is PARTIAL" in manifest.reportability_issues()


def test_manifest_reportability_requires_provenance_and_clean_validation() -> None:
    manifest = RunManifest(
        session_id=new_ulid(),
        experiment_id="v",
        config_sha256="a" * 64,
        plan_sha256="b" * 64,
        git_commit="c" * 40,
        git_dirty=False,
        image_digests={
            "shaper": "sha256:s",
            "loadgen": "sha256:l",
            "capture": "sha256:c",
            "vllm": "sha256:v",
        },
        model_id="m",
        model_revision="model-commit",
        tokenizer_revision="tokenizer-commit",
        workload_version="v1",
        workload_sha256="d" * 64,
        policy_set_sha256="e" * 64,
        seeds={"plan": 1},
        stream_interval=1,
        concurrency=1,
        environment={},
        timing={},
        planned=1,
        completed=1,
        capture_drops=0,
        validation={"tshark": True, "emissions": True},
        status=SessionStatus.VALID,
    )
    assert manifest.reportability_issues() == []
    failed = manifest.model_copy(update={"validation": {"tshark": False}})
    assert failed.reportability_issues() == ["validation failed: tshark"]


def test_schemas_and_access_boundaries(tmp_path: Path) -> None:
    assert OBSERVER_TABLES.isdisjoint(ORACLE_TABLES)
    assert all(
        isinstance(schema, pa.Schema) and schema.field("session_id").type == pa.string()
        for schema in SCHEMAS.values()
    )
    assert schema_for("records").field("length").type == pa.uint16()
    with pytest.raises(OracleAccessError):
        load(tmp_path, "session", "emissions")
    with pytest.raises(OracleAccessError):
        load(tmp_path, "../restricted", "records")


def test_atomic_write_rejects_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "artifact.json"
    atomic_write(target, b"first")
    with pytest.raises(ValidationError):
        atomic_write(target, b"second")
    assert target.read_bytes() == b"first"


def test_bounded_parquet_batches_preserve_rows(tmp_path: Path) -> None:
    path = tmp_path / "flow_map.parquet"
    with BatchTableWriter(path, schema_for("flow_map"), batch_size=3) as writer:
        for flow_id in range(11):
            writer.add({"session_id": "s", "flow_id": flow_id, "request_id": str(flow_id)})
    assert [row["flow_id"] for row in read_table(path).to_pylist()] == list(range(11))
    with pytest.raises(ValidationError), BatchTableWriter(path, schema_for("flow_map")):
        pass
