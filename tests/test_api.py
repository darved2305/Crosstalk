from collections.abc import Mapping
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from crosstalk.api.app import ApiSettings, create_app
from crosstalk.api.store import ControlStore, NotFoundError
from crosstalk.contracts.config import ExperimentConfig

TOKEN = "o" * 32
ORACLE_TOKEN = "r" * 32
EXPERIMENT_ID = "01K00000000000000000000000"
SESSION_ID = "01K00000000000000000000001"
JOB_ID = "01K00000000000000000000002"


class FakeControlStore(ControlStore):
    def __init__(self) -> None:
        self.ready = True
        self.runs: dict[tuple[str, str], Mapping[str, object]] = {}

    def healthy(self) -> bool:
        return self.ready

    def create_experiment(self, config: ExperimentConfig) -> Mapping[str, object]:
        return {"id": EXPERIMENT_ID, "name": config.name, "config_hash": config.sha256()}

    def create_run(
        self, experiment_id: str, idempotency_key: str, code_commit: str
    ) -> tuple[Mapping[str, object], bool]:
        del code_commit
        key = (experiment_id, idempotency_key)
        replayed = key in self.runs
        response = self.runs.setdefault(
            key, {"experiment_id": experiment_id, "session_ids": [SESSION_ID]}
        )
        return response, replayed

    def list_experiments(
        self, cursor: str | None, limit: int
    ) -> tuple[list[Mapping[str, object]], str | None]:
        del cursor, limit
        return [self._summary()], None

    def get_experiment(self, experiment_id: str) -> Mapping[str, object]:
        if experiment_id != EXPERIMENT_ID:
            raise NotFoundError("Experiment does not exist")
        return {
            **self._summary(),
            "config": minimal_config(),
            "sessions": [self.get_session(SESSION_ID)],
        }

    def get_session(self, session_id: str) -> Mapping[str, object]:
        if session_id != SESSION_ID:
            raise NotFoundError("Session does not exist")
        return {
            "id": SESSION_ID,
            "experiment_id": EXPERIMENT_ID,
            "state": "PLANNED",
            "cancel_requested": False,
            "jobs": [],
        }

    def cancel_experiment(self, experiment_id: str) -> Mapping[str, object]:
        return {"id": experiment_id, "state": "cancelled"}

    def retry_job(self, job_id: str) -> Mapping[str, object]:
        return {"id": job_id, "state": "queued"}

    @staticmethod
    def _summary() -> Mapping[str, object]:
        now = datetime.now(UTC)
        return {
            "id": EXPERIMENT_ID,
            "name": "api-test",
            "config_hash": "a" * 64,
            "state": "running",
            "created_at": now,
            "updated_at": now,
        }


def minimal_config() -> dict[str, object]:
    return {
        "name": "api-test",
        "sessions": [
            {
                "experiment_id": "api-test",
                "requests": 1,
                "concurrency": 1,
                "seed": 0,
            }
        ],
    }


def client(store: FakeControlStore | None = None) -> TestClient:
    settings = ApiSettings(operator_token=TOKEN, oracle_token=ORACLE_TOKEN, code_commit="abc")
    return TestClient(create_app(store or FakeControlStore(), settings))


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_database_readiness() -> None:
    store = FakeControlStore()
    with client(store) as api:
        assert api.get("/healthz").json() == {"status": "ok"}
        assert api.get("/readyz").status_code == 200
        store.ready = False
        assert api.get("/readyz").status_code == 503
        assert "crosstalk_api_ready 0" in api.get("/metrics").text


def test_controls_require_bearer_token() -> None:
    with client() as api:
        response = api.get("/v1/experiments")
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert "traceback" not in response.text.lower()


def test_create_and_idempotent_run_replay() -> None:
    with client() as api:
        created = api.post("/v1/experiments", headers=auth(), json=minimal_config())
        assert created.status_code == 201
        path = f"/v1/experiments/{EXPERIMENT_ID}/runs"
        first = api.post(path, headers={**auth(), "Idempotency-Key": "same"})
        replay = api.post(path, headers={**auth(), "Idempotency-Key": "same"})
    assert first.status_code == 202 and replay.status_code == 202
    assert first.json()["session_ids"] == replay.json()["session_ids"]
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is True


def test_detail_list_and_controls() -> None:
    with client() as api:
        listing = api.get("/v1/experiments?limit=10", headers=auth())
        detail = api.get(f"/v1/experiments/{EXPERIMENT_ID}", headers=auth())
        session = api.get(f"/v1/sessions/{SESSION_ID}", headers=auth())
        cancelled = api.post(f"/v1/experiments/{EXPERIMENT_ID}/cancel", headers=auth())
        retried = api.post(f"/v1/jobs/{JOB_ID}/retry", headers=auth())
    assert listing.status_code == detail.status_code == session.status_code == 200
    assert listing.json()["items"][0]["state"] == "running"
    assert cancelled.json()["state"] == "cancelled"
    assert retried.json()["state"] == "queued"


def test_errors_are_problem_json_and_validate_ids() -> None:
    with client() as api:
        malformed = api.get("/v1/experiments/not-an-id", headers=auth())
        missing = api.get(f"/v1/experiments/{'01K00000000000000000000003'}", headers=auth())
    assert malformed.status_code == 422
    assert missing.status_code == 404
    assert all(
        response.headers["content-type"] == "application/problem+json"
        for response in (malformed, missing)
    )


def test_request_size_limit_and_missing_idempotency_key() -> None:
    with client() as api:
        oversized = api.post(
            "/v1/experiments", headers={**auth(), "Content-Length": str(300_000)}, content=b"{}"
        )
        missing_key = api.post(f"/v1/experiments/{EXPERIMENT_ID}/runs", headers=auth())
    assert oversized.status_code == 413
    assert missing_key.status_code == 422


def test_openapi_has_stable_control_contracts() -> None:
    document = create_app(
        FakeControlStore(), ApiSettings(operator_token=TOKEN, oracle_token=ORACLE_TOKEN)
    ).openapi()
    paths = document["paths"]
    assert "/v1/experiments" in paths
    assert "/v1/experiments/{experiment_id}/runs" in paths
    assert (
        paths["/v1/experiments/{experiment_id}/runs"]["post"]["parameters"][1]["name"]
        == "Idempotency-Key"
    )
