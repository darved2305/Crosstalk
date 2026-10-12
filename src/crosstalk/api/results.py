import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, TypedDict, cast

import duckdb

from crosstalk.contracts.manifest import RunManifest


class ResultNotFoundError(Exception):
    pass


class ResultDataError(Exception):
    pass


class ResultProvenance(TypedDict):
    reportable: bool | None
    warning: str


class ResultStore(Protocol):
    def provenance(self, experiment_id: str, experiment_name: str) -> ResultProvenance: ...
    def evaluations(
        self, experiment_id: str, experiment_name: str | None = None
    ) -> list[Mapping[str, object]]: ...
    def frontier(
        self, experiment_id: str, setting: str, experiment_name: str | None = None
    ) -> list[Mapping[str, object]]: ...
    def trace(
        self, session_id: str, request_id: str, layer: str, limit: int = 20_000
    ) -> Mapping[str, object]: ...
    def emissions(self, session_id: str, request_id: str) -> list[Mapping[str, object]]: ...
    def report(self, experiment_name: str) -> str: ...


class DuckDBResultStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def provenance(self, experiment_id: str, experiment_name: str) -> ResultProvenance:
        try:
            path = self._experiment_file(experiment_id, experiment_name, "summary.json")
        except ResultNotFoundError:
            return {"reportable": None, "warning": "Reportability has not been verified."}
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise ResultDataError("Unable to read experiment provenance") from error
        if not isinstance(summary, dict):
            raise ResultDataError("Experiment summary must be an object")
        reportable = cast(dict[str, object], summary).get("reportable")
        if not isinstance(reportable, bool):
            raise ResultDataError("Experiment summary is missing boolean reportability")
        return {
            "reportable": reportable,
            "warning": "Validated experiment artifact."
            if reportable
            else (
                "NOT REPORTABLE: development measurements from unvalidated sessions. "
                "A mock upstream or synthetic packet segmentation may be present; "
                "consult the report and session manifests before interpreting results."
            ),
        }

    def evaluations(
        self, experiment_id: str, experiment_name: str | None = None
    ) -> list[Mapping[str, object]]:
        path = self._experiment_file(
            experiment_id, experiment_name, "evaluations", "evaluations.parquet"
        )
        return self._rows(
            f"SELECT * FROM read_parquet({self._literal(path)}) ORDER BY regime, arm, setting, model, metric"
        )

    def frontier(
        self, experiment_id: str, setting: str, experiment_name: str | None = None
    ) -> list[Mapping[str, object]]:
        path = self._experiment_file(experiment_id, experiment_name, "frontier", "frontier.parquet")
        return self._rows(
            f"SELECT * FROM read_parquet({self._literal(path)}) WHERE setting = ? ORDER BY concurrency, condition",
            [setting],
        )

    def trace(
        self, session_id: str, request_id: str, layer: str, limit: int = 20_000
    ) -> Mapping[str, object]:
        if layer not in {"records", "packets"}:
            raise ValueError("layer must be records or packets")
        if not 100 <= limit <= 20_000:
            raise ValueError("trace limit must lie in [100, 20000]")
        observer = self._directory("sessions", session_id, "observer")
        provenance: ResultProvenance = {
            "reportable": None,
            "warning": "Session provenance has not been verified.",
        }
        manifest_path = observer.parent / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = RunManifest.model_validate_json(manifest_path.read_text("utf-8"))
            except (ValueError, OSError) as error:
                raise ResultDataError("Unable to read session provenance") from error
            issues = manifest.reportability_issues()
            relay = manifest.environment.get("capture_interface") == "recording-relay"
            provenance = {
                "reportable": not issues and not relay,
                "warning": (
                    "NOT REPORTABLE: recorded TLS ciphertext with synthetic TCP packet segmentation; "
                    "mock upstream development run."
                    if relay
                    else "NOT REPORTABLE: " + "; ".join(issues)
                    if issues
                    else "Validated session manifest."
                ),
            }
        flow_map = self._existing(observer / "flow_map.parquet")
        table = self._existing(observer / f"{layer}.parquet")
        if layer == "records":
            timestamp, size, ordering = "r.t_end_ns", "r.length", "r.t_end_ns, r.idx"
        else:
            timestamp, size, ordering = "r.ts_ns", "r.tcp_len", "r.ts_ns, r.rel_seq"
        source = (
            f"SELECT {timestamp} AS ts_ns, "
            f"CASE WHEN r.dir < 0 THEN -CAST({size} AS BIGINT) ELSE CAST({size} AS BIGINT) END AS value "
            f"FROM read_parquet({self._literal(table)}) r "
            f"JOIN read_parquet({self._literal(flow_map)}) m USING (session_id, flow_id) "
            f"WHERE m.request_id = ? ORDER BY {ordering}"
        )
        try:
            with duckdb.connect(":memory:") as connection:
                count_row = connection.execute(
                    f"SELECT count(*) FROM ({source})", [request_id]
                ).fetchone()
                if count_row is None:
                    raise ResultDataError("Trace count query returned no row")
                count = int(count_row[0])
                if count <= limit:
                    rows = self._query(
                        connection,
                        f"SELECT ts_ns AS t_start_ns, ts_ns AS t_end_ns, value AS min_value, "
                        f"value AS max_value, 1 AS count FROM ({source})",
                        [request_id],
                    )
                else:
                    rows = self._query(
                        connection,
                        "WITH points AS ("
                        f"SELECT *, row_number() OVER () - 1 AS rn FROM ({source})"
                        "), buckets AS ("
                        "SELECT floor(rn * ? / ?)::BIGINT AS bucket, * FROM points"
                        ") SELECT min(t_start) AS t_start_ns, max(t_start) AS t_end_ns, "
                        "min(value) AS min_value, max(value) AS max_value, count(*) AS count "
                        "FROM (SELECT bucket, ts_ns AS t_start, value FROM buckets) "
                        "GROUP BY bucket ORDER BY bucket",
                        [request_id, limit, count],
                    )
        except duckdb.Error as error:
            raise ResultDataError(f"Unable to query trace data: {error}") from error
        return {
            **provenance,
            "session_id": session_id,
            "request_id": request_id,
            "layer": layer,
            "source_points": count,
            "downsampled": count > limit,
            "points": rows,
        }

    def emissions(self, session_id: str, request_id: str) -> list[Mapping[str, object]]:
        path = self._file("sessions", session_id, "oracle", "emissions.parquet")
        return self._rows(
            f"SELECT seq, t_emit_ns, kind, framed_len, content_bytes, pad_bytes, n_events, "
            f"queue_delay_ns, timer_lateness_ns, arm FROM read_parquet({self._literal(path)}) "
            "WHERE request_id = ? ORDER BY seq",
            [request_id],
        )

    def report(self, experiment_name: str) -> str:
        path = self._file("experiments", experiment_name, "report.md")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeError as error:
            raise ResultDataError("Report artifact is not valid UTF-8") from error

    def _directory(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts).resolve()
        if not path.is_relative_to(self.root):
            raise ResultNotFoundError("Artifact path escapes the configured root")
        return path

    def _file(self, *parts: str) -> Path:
        return self._existing(self._directory(*parts))

    def _experiment_file(
        self, experiment_id: str, experiment_name: str | None, *parts: str
    ) -> Path:
        try:
            return self._file("experiments", experiment_id, *parts)
        except ResultNotFoundError:
            if experiment_name is None:
                raise
            return self._file("experiments", experiment_name, *parts)

    @staticmethod
    def _existing(path: Path) -> Path:
        if not path.is_file():
            raise ResultNotFoundError("Result artifact does not exist")
        return path

    @staticmethod
    def _literal(path: Path) -> str:
        return "'" + path.as_posix().replace("'", "''") + "'"

    def _rows(self, sql: str, parameters: list[object] | None = None) -> list[Mapping[str, object]]:
        try:
            with duckdb.connect(":memory:") as connection:
                return self._query(connection, sql, parameters)
        except duckdb.Error as error:
            raise ResultDataError(f"Unable to query result data: {error}") from error

    @staticmethod
    def _query(
        connection: duckdb.DuckDBPyConnection,
        sql: str,
        parameters: list[object] | None = None,
    ) -> list[Mapping[str, object]]:
        cursor = connection.execute(sql, parameters or [])
        names = [item[0] for item in cursor.description]
        return [
            {
                name: cast(
                    object, None if isinstance(value, float) and not math.isfinite(value) else value
                )
                for name, value in zip(names, row, strict=True)
            }
            for row in cursor.fetchall()
        ]
