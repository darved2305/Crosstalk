import hmac
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from crosstalk.api.results import (
    DuckDBResultStore,
    ResultDataError,
    ResultNotFoundError,
    ResultStore,
)
from crosstalk.api.schemas import (
    ControlResult,
    EmissionResults,
    EmissionRow,
    EvaluationResults,
    EvaluationRow,
    ExperimentCreated,
    ExperimentDetail,
    ExperimentPage,
    ExperimentSummary,
    FrontierResults,
    FrontierRow,
    Problem,
    RunCreated,
    SessionView,
    TraceResults,
)
from crosstalk.api.store import ConflictError, ControlStore, NotFoundError, PostgresControlStore
from crosstalk.common.ids import is_ulid
from crosstalk.contracts.config import ExperimentConfig

MAX_REQUEST_BYTES = 256 * 1024


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CT_API_", extra="ignore")

    database_url: str = "postgresql+psycopg://crosstalk:crosstalk@localhost:5432/crosstalk"
    operator_token: str = Field(min_length=32)
    oracle_token: str = Field(min_length=32)
    code_commit: str = "unknown"
    artifacts_root: Path = Path("artifacts")


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, maximum: int = MAX_REQUEST_BYTES) -> None:
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        length = headers.get(b"content-length")
        if length is not None and int(length) > self.maximum:
            await self._reject(scope, receive, send)
            return
        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.maximum:
                    raise RequestTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestTooLargeError:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        del receive
        request = Request(scope)
        response = problem_response(
            413, "Request too large", "Request body exceeds 256 KiB", request
        )

        async def empty_receive() -> Message:
            return {"type": "http.disconnect"}

        await response(scope, empty_receive, send)


class RequestTooLargeError(Exception):
    pass


def problem_response(status: int, title: str, detail: str, request: Request) -> JSONResponse:
    problem = Problem(
        type=f"https://crosstalk.dev/problems/{status}",
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
    )
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


def create_app(
    store: ControlStore | None = None,
    settings: ApiSettings | None = None,
    result_store: ResultStore | None = None,
) -> FastAPI:
    resolved = settings or ApiSettings()  # pyright: ignore[reportCallIssue]
    control = store or PostgresControlStore(
        create_engine(resolved.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    )
    result_data = result_store or DuckDBResultStore(resolved.artifacts_root)
    app = FastAPI(title="Crosstalk Control API", version="1.0.0")
    app.add_middleware(BodyLimitMiddleware)

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, error: NotFoundError) -> JSONResponse:
        return problem_response(404, "Not found", str(error), request)

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, error: ConflictError) -> JSONResponse:
        return problem_response(409, "Conflict", str(error), request)

    @app.exception_handler(InputError)
    async def invalid_input(request: Request, error: InputError) -> JSONResponse:
        return problem_response(422, "Invalid request", str(error), request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        return problem_response(422, "Invalid request", detail, request)

    @app.exception_handler(ResultNotFoundError)
    async def result_not_found(request: Request, error: ResultNotFoundError) -> JSONResponse:
        return problem_response(404, "Result not found", str(error), request)

    @app.exception_handler(ResultDataError)
    async def invalid_result(request: Request, error: ResultDataError) -> JSONResponse:
        return problem_response(422, "Invalid result artifact", str(error), request)

    def scopes(authorization: Annotated[str | None, Header()] = None) -> frozenset[str]:
        if authorization is None or not authorization.startswith("Bearer "):
            raise AuthError("A bearer token is required")
        token = authorization.removeprefix("Bearer ")
        if hmac.compare_digest(token, resolved.oracle_token):
            return frozenset({"operator", "oracle"})
        if hmac.compare_digest(token, resolved.operator_token):
            return frozenset({"operator"})
        raise AuthError("The bearer token is invalid")

    @app.exception_handler(AuthError)
    async def unauthorized(request: Request, error: AuthError) -> JSONResponse:
        return problem_response(401, "Unauthorized", str(error), request)

    @app.get("/healthz", include_in_schema=False)
    def health() -> Mapping[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def ready() -> JSONResponse:
        if control.healthy():
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not_ready"}, status_code=503)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> PlainTextResponse:
        ready_value = 1 if control.healthy() else 0
        return PlainTextResponse(
            f"# HELP crosstalk_api_ready Database readiness.\n"
            f"# TYPE crosstalk_api_ready gauge\n"
            f"crosstalk_api_ready {ready_value}\n",
            media_type="text/plain; version=0.0.4",
        )

    @app.post("/v1/experiments", response_model=ExperimentCreated, status_code=201)
    def create_experiment(
        config: ExperimentConfig,
        auth: Annotated[frozenset[str], Depends(scopes)],
    ) -> ExperimentCreated:
        del auth
        return ExperimentCreated.model_validate(control.create_experiment(config))

    @app.post("/v1/experiments/{experiment_id}/runs", response_model=RunCreated, status_code=202)
    def create_run(
        experiment_id: str,
        auth: Annotated[frozenset[str], Depends(scopes)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
        ],
    ) -> RunCreated:
        del auth
        require_ulid(experiment_id, "experiment_id")
        response, replayed = control.create_run(
            experiment_id, idempotency_key, resolved.code_commit
        )
        return RunCreated.model_validate({**response, "replayed": replayed})

    @app.get("/v1/experiments", response_model=ExperimentPage)
    def list_experiments(
        auth: Annotated[frozenset[str], Depends(scopes)],
        cursor: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> ExperimentPage:
        del auth
        if cursor is not None:
            require_ulid(cursor, "cursor")
        rows, next_cursor = control.list_experiments(cursor, limit)
        return ExperimentPage(
            items=[ExperimentSummary.model_validate(row) for row in rows],
            next_cursor=next_cursor,
        )

    @app.get("/v1/experiments/{experiment_id}", response_model=ExperimentDetail)
    def get_experiment(
        experiment_id: str, auth: Annotated[frozenset[str], Depends(scopes)]
    ) -> ExperimentDetail:
        del auth
        require_ulid(experiment_id, "experiment_id")
        return ExperimentDetail.model_validate(control.get_experiment(experiment_id))

    @app.get("/v1/sessions/{session_id}", response_model=SessionView)
    def get_session(
        session_id: str, auth: Annotated[frozenset[str], Depends(scopes)]
    ) -> SessionView:
        del auth
        require_ulid(session_id, "session_id")
        return SessionView.model_validate(control.get_session(session_id))

    @app.post("/v1/experiments/{experiment_id}/cancel", response_model=ControlResult)
    def cancel_experiment(
        experiment_id: str, auth: Annotated[frozenset[str], Depends(scopes)]
    ) -> ControlResult:
        del auth
        require_ulid(experiment_id, "experiment_id")
        return ControlResult.model_validate(control.cancel_experiment(experiment_id))

    @app.post("/v1/jobs/{job_id}/retry", response_model=ControlResult)
    def retry_job(job_id: str, auth: Annotated[frozenset[str], Depends(scopes)]) -> ControlResult:
        del auth
        require_ulid(job_id, "job_id")
        return ControlResult.model_validate(control.retry_job(job_id))

    @app.get("/v1/experiments/{experiment_id}/results", response_model=EvaluationResults)
    def experiment_results(
        experiment_id: str, auth: Annotated[frozenset[str], Depends(scopes)]
    ) -> EvaluationResults:
        del auth
        require_ulid(experiment_id, "experiment_id")
        return EvaluationResults(
            experiment_id=experiment_id,
            rows=[
                EvaluationRow.model_validate(row) for row in result_data.evaluations(experiment_id)
            ],
        )

    @app.get("/v1/experiments/{experiment_id}/frontier", response_model=FrontierResults)
    def experiment_frontier(
        experiment_id: str,
        auth: Annotated[frozenset[str], Depends(scopes)],
        setting: Annotated[str, Query(pattern="^(static|adaptive)$")] = "adaptive",
        cost: Annotated[str, Query(pattern="^(ttft_p95|itl_p99|bandwidth|cpu)$")] = "bandwidth",
    ) -> FrontierResults:
        del auth
        require_ulid(experiment_id, "experiment_id")
        return FrontierResults(
            experiment_id=experiment_id,
            setting=setting,
            cost=cost,
            rows=[
                FrontierRow.model_validate(row)
                for row in result_data.frontier(experiment_id, setting)
            ],
        )

    @app.get("/v1/sessions/{session_id}/traces/{request_id}", response_model=TraceResults)
    def trace(
        session_id: str,
        request_id: str,
        auth: Annotated[frozenset[str], Depends(scopes)],
        layer: Annotated[str, Query(pattern="^(records|packets)$")] = "records",
    ) -> TraceResults:
        del auth
        require_ulid(session_id, "session_id")
        require_ulid(request_id, "request_id")
        return TraceResults.model_validate(result_data.trace(session_id, request_id, layer))

    @app.get(
        "/v1/sessions/{session_id}/traces/{request_id}/emissions",
        response_model=EmissionResults,
    )
    def emissions(
        session_id: str,
        request_id: str,
        auth: Annotated[frozenset[str], Depends(scopes)],
    ) -> EmissionResults:
        require_ulid(session_id, "session_id")
        require_ulid(request_id, "request_id")
        if "oracle" not in auth:
            raise ScopeError("The oracle scope is required for emission data")
        return EmissionResults(
            session_id=session_id,
            request_id=request_id,
            rows=[
                EmissionRow.model_validate(row)
                for row in result_data.emissions(session_id, request_id)
            ],
        )

    return app


class AuthError(Exception):
    pass


class InputError(Exception):
    pass


class ScopeError(AuthError):
    pass


def require_ulid(value: str, field: str) -> None:
    if not is_ulid(value):
        raise InputError(f"{field} must be a ULID")


def main() -> None:
    import uvicorn

    uvicorn.run("crosstalk.api.app:create_app", factory=True, host="0.0.0.0", port=8080)


if os.getenv("CT_API_EAGER_APP") == "1":
    app = create_app()
