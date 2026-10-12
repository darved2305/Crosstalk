import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Ban,
  FileText,
  Play,
  RefreshCw,
} from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api, apiText, errorMessage } from "../lib/client";
import {
  EXPERIMENT_STAGES,
  SESSION_STAGES,
  allJobs,
  displayState,
  isLive,
  pollInterval,
  reportPublished,
  reportability,
} from "../lib/pipeline";
import {
  controlResult,
  experimentDetail,
  runCreated,
  ulid,
  type ExperimentDetail,
  type Job,
} from "../lib/schemas";
import { ActivityTimeline } from "../components/activity-timeline";
import { MarkdownReport } from "../components/markdown-report";
import { StagePipeline } from "../components/stage-pipeline";
import { Empty, Segments, State, Status } from "./common";
import { formatDate, formatTime, shortId } from "./format";

type Feedback = { tone: "info" | "error"; text: string } | null;

function NotReportableBanner({ detail }: { detail: ExperimentDetail }) {
  const verdict = reportability(detail);
  if (verdict.reportable === null) return null;
  if (verdict.reportable)
    return (
      <section
        className="reportBanner ok"
        aria-labelledby="reportability-title"
      >
        <h2 id="reportability-title">Reportable</h2>
        <p>Every session passed validation.</p>
      </section>
    );
  return (
    <section className="reportBanner" aria-labelledby="reportability-title">
      <AlertTriangle size={22} aria-hidden="true" />
      <div>
        <h2 id="reportability-title">NOT REPORTABLE</h2>
        <p>
          {verdict.invalidSessions.length
            ? `${verdict.invalidSessions.length} of ${detail.sessions.length} session(s) did not validate. `
            : ""}
          These measurements exercise the pipeline and must not be cited as
          evidence.
        </p>
        <ul>
          {verdict.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function ReportSection({ detail }: { detail: ExperimentDetail }) {
  const published = reportPublished(detail);
  const latest =
    detail.events.find((e) => e.kind === "report_published")?.id ?? "job";
  const report = useQuery({
    queryKey: ["report", detail.id, latest],
    queryFn: () => apiText(`/v1/experiments/${detail.id}/report`),
    enabled: published,
  });
  const reportJob = detail.jobs.filter((j) => j.kind === "report").at(-1);
  if (!published)
    return (
      <Empty>
        {reportJob && ["failed", "dead"].includes(reportJob.state)
          ? "The report stage failed. Retry it from the experiment stages above."
          : isLive(detail)
            ? "The report appears here automatically once the report stage succeeds."
            : "No report has been published for this experiment."}
      </Empty>
    );
  return (
    <State
      loading={report.isLoading}
      error={report.error}
      loadingMessage="Loading report…"
    >
      {report.data && (
        <div className="reportPreview" data-testid="experiment-report">
          <MarkdownReport markdown={report.data} />
          <div className="reportPreviewFoot">
            <Link
              className="outlineButton"
              to={`/reports?experiment=${detail.id}`}
            >
              <FileText size={16} aria-hidden="true" /> Open full report
            </Link>
          </div>
        </div>
      )}
    </State>
  );
}

export function Experiment() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const valid = ulid.safeParse(id).success;
  const client = useQueryClient();
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const runKey = useRef<string | null>(null);
  const query = useQuery({
    queryKey: ["experiment", id],
    queryFn: () => api(`/v1/experiments/${id}`, experimentDetail),
    enabled: valid,
    refetchInterval: (q) => pollInterval(q.state.data),
    refetchIntervalInBackground: false,
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["experiment", id] });
    void client.invalidateQueries({ queryKey: ["experiments"] });
  };
  const run = useMutation({
    mutationFn: () => {
      // Reuse the key after a transport failure so a resend cannot start a second run.
      const key = runKey.current ?? crypto.randomUUID();
      runKey.current = key;
      return api(`/v1/experiments/${id}/runs`, runCreated, {
        method: "POST",
        headers: { "Idempotency-Key": key },
      });
    },
    onMutate: () => setFeedback(null),
    onSuccess: (data) => {
      runKey.current = null;
      setFeedback({
        tone: "info",
        text: data.replayed
          ? "This run was already accepted; showing its sessions."
          : `Run started: ${data.session_ids.length} session(s) enqueued.`,
      });
      refresh();
    },
    onError: (error) => {
      if (!(error instanceof ApiError && error.status === 0))
        runKey.current = null;
      setFeedback({
        tone: "error",
        text: `Could not start run. ${errorMessage(error)}`,
      });
    },
  });
  const cancel = useMutation({
    mutationFn: () =>
      api(`/v1/experiments/${id}/cancel`, controlResult, { method: "POST" }),
    onMutate: () => setFeedback(null),
    onSuccess: (data) => {
      setConfirmCancel(false);
      setFeedback({
        tone: "info",
        text: `Cancellation requested; experiment is ${data.state}.`,
      });
      refresh();
    },
    onError: (error) =>
      setFeedback({
        tone: "error",
        text: `Could not cancel. ${errorMessage(error)}`,
      }),
  });
  const retry = useMutation({
    mutationFn: (job: Job) =>
      api(`/v1/jobs/${job.id}/retry`, controlResult, { method: "POST" }),
    onMutate: () => setFeedback(null),
    onSuccess: (data, job) => {
      setFeedback({
        tone: "info",
        text: `Retry requested for ${job.kind}; job is ${data.state}.`,
      });
      refresh();
    },
    onError: (error, job) =>
      setFeedback({
        tone: "error",
        text: `Could not retry ${job.kind}. ${errorMessage(error)}`,
      }),
  });

  if (!valid)
    return <State error={new Error("Experiment ID must be a ULID.")} />;
  const data = query.data;
  const live = isLive(data);
  const jobs = data ? allJobs(data) : [];
  const completed = jobs.filter((j) => j.state === "succeeded").length;
  const policySet = data?.config.policy_set as
    { arm: string; kind: string }[] | undefined;
  const kind = typeof data?.config.kind === "string" ? data.config.kind : null;
  const labels = new Map(
    data?.sessions.map((s, i) => [s.id, `Session ${i + 1}`]) ?? [],
  );
  const sessionParam = params.get("session");
  const selected =
    sessionParam && data?.sessions.some((s) => s.id === sessionParam)
      ? sessionParam
      : "all";
  const sessions =
    data?.sessions.filter((s) => selected === "all" || s.id === selected) ?? [];
  const events =
    data?.events.filter(
      (e) => selected === "all" || !e.session_id || e.session_id === selected,
    ) ?? [];
  const busy = run.isPending || cancel.isPending;

  return (
    <>
      <Link className="backLink" to="/">
        <ArrowLeft size={16} aria-hidden="true" /> Experiments
      </Link>
      <State
        loading={query.isLoading}
        error={data ? null : query.error}
        loadingMessage="Loading experiment…"
      >
        {data && (
          <>
            <div className="detailHead">
              <div>
                <p className="eyebrow">
                  EXPERIMENT{kind ? ` · ${kind.toUpperCase()}` : ""} /{" "}
                  <span className="mono">{id}</span>
                </p>
                <h1>{data.name}</h1>
                <div className="detailMeta">
                  <Status value={displayState(data)} label="Experiment state" />
                  <span>Created {formatDate(data.created_at)}</span>
                  <span>Updated {formatDate(data.updated_at)}</span>
                  <span className="mono">
                    Config {data.config_hash.slice(0, 12)}
                  </span>
                </div>
              </div>
              <div className="detailActions">
                <button
                  type="button"
                  className="primary"
                  onClick={() => run.mutate()}
                  disabled={busy || live || data.state === "cancelled"}
                  aria-busy={run.isPending}
                  title={
                    live
                      ? "A run is already in progress"
                      : data.state === "cancelled"
                        ? "Cancelled experiments cannot start new runs"
                        : undefined
                  }
                >
                  <Play size={16} aria-hidden="true" />
                  {run.isPending ? "Starting…" : "Start run"}
                </button>
                <button
                  type="button"
                  className="outlineButton danger"
                  onClick={() => setConfirmCancel(true)}
                  disabled={busy || !live || confirmCancel}
                  title={live ? undefined : "Nothing is queued or running"}
                >
                  <Ban size={16} aria-hidden="true" /> Cancel
                </button>
              </div>
            </div>
            {confirmCancel && (
              <div
                className="confirmBar"
                role="alertdialog"
                aria-labelledby="cancel-q"
              >
                <p id="cancel-q">
                  Cancel <strong>{data.name}</strong>? Queued and running jobs
                  stop and the experiment becomes cancelled. This cannot be
                  undone.
                </p>
                <div>
                  <button
                    type="button"
                    className="outlineButton"
                    onClick={() => setConfirmCancel(false)}
                    disabled={cancel.isPending}
                    autoFocus
                  >
                    Keep running
                  </button>
                  <button
                    type="button"
                    className="primary dangerFill"
                    onClick={() => cancel.mutate()}
                    disabled={cancel.isPending}
                    aria-busy={cancel.isPending}
                  >
                    {cancel.isPending ? "Cancelling…" : "Confirm cancel"}
                  </button>
                </div>
              </div>
            )}
            <div aria-live="polite" role="status" className="liveRegion">
              {feedback?.tone === "info" && (
                <div className="notice">{feedback.text}</div>
              )}
            </div>
            {feedback?.tone === "error" && (
              <div className="notice error" role="alert">
                {feedback.text}
              </div>
            )}
            <p className={`liveBar ${live ? "on" : ""}`}>
              {live ? (
                <>
                  <RefreshCw size={14} className="spin" aria-hidden="true" />{" "}
                  Pipeline running · refreshing every 2 s
                </>
              ) : (
                <>Pipeline idle · state {displayState(data)}</>
              )}
              <span>
                Last refreshed{" "}
                {formatTime(new Date(query.dataUpdatedAt).toISOString())}
                {query.isFetching ? " · updating…" : ""}
              </span>
            </p>
            {query.isRefetchError && (
              <div className="notice error" role="alert">
                Refresh failed; showing the last known state.{" "}
                {errorMessage(query.error)}
              </div>
            )}
            <NotReportableBanner detail={data} />
            <div className="summaryGrid">
              <div className="summaryBox">
                <span>Sessions</span>
                <strong>{data.sessions.length}</strong>
              </div>
              <div className="summaryBox">
                <span>Validated (VALID)</span>
                <strong>
                  {data.sessions.filter((s) => s.state === "VALID").length}
                </strong>
              </div>
              <div className="summaryBox">
                <span>Jobs succeeded</span>
                <strong>
                  {completed} / {jobs.length}
                </strong>
              </div>
              <div className="summaryBox">
                <span>Pipeline</span>
                <strong>
                  {jobs.length
                    ? Math.round((100 * completed) / jobs.length)
                    : 0}
                  %
                </strong>
                <Segments value={jobs.length ? completed / jobs.length : 0} />
              </div>
            </div>

            <div className="detailColumns">
              <div>
                <div className="sectionLine">
                  <h2 id="sessions-title">Session pipelines</h2>
                  {data.sessions.length > 1 && (
                    <label className="inlineSelect">
                      Session
                      <select
                        value={selected}
                        onChange={(e) => {
                          const next = new URLSearchParams(params);
                          if (e.target.value === "all") next.delete("session");
                          else next.set("session", e.target.value);
                          setParams(next, { replace: true });
                        }}
                      >
                        <option value="all">
                          All sessions ({data.sessions.length})
                        </option>
                        {data.sessions.map((s, i) => (
                          <option key={s.id} value={s.id}>
                            Session {i + 1} · {s.state} · {shortId(s.id)}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </div>
                {!data.sessions.length ? (
                  <Empty>
                    No sessions yet. Start a run to plan and collect sessions.
                  </Empty>
                ) : (
                  sessions.map((session) => (
                    <section
                      className="sessionPanel"
                      key={session.id}
                      aria-label={`${labels.get(session.id)} pipeline`}
                    >
                      <div className="sessionTop">
                        <div>
                          <h3>{labels.get(session.id)}</h3>
                          <p className="mono">{session.id}</p>
                          {session.cancel_requested && (
                            <p>Cancellation requested</p>
                          )}
                        </div>
                        <div className="sessionActions">
                          <Status value={session.state} label="Session state" />
                          <Link
                            className="textLink"
                            to={`/traces?experiment=${id}&session=${session.id}`}
                          >
                            Traces <ArrowRight size={14} aria-hidden="true" />
                          </Link>
                        </div>
                      </div>
                      <StagePipeline
                        label={`${labels.get(session.id)} stages`}
                        jobs={session.jobs}
                        stages={SESSION_STAGES}
                        onRetry={(job) => retry.mutate(job)}
                        retryingId={
                          retry.isPending ? (retry.variables?.id ?? null) : null
                        }
                        retryDisabled={retry.isPending}
                      />
                    </section>
                  ))
                )}
                <div className="sectionLine">
                  <h2>Experiment stages</h2>
                  <span>
                    Analysis and report run after every session validates
                  </span>
                </div>
                <section
                  className="sessionPanel"
                  aria-label="Experiment stages"
                >
                  <StagePipeline
                    label="Experiment stages"
                    jobs={data.jobs}
                    stages={EXPERIMENT_STAGES}
                    onRetry={(job) => retry.mutate(job)}
                    retryingId={
                      retry.isPending ? (retry.variables?.id ?? null) : null
                    }
                    retryDisabled={retry.isPending}
                  />
                </section>
              </div>
              <aside
                aria-labelledby="activity-title"
                className="activityColumn"
              >
                <div className="sectionLine">
                  <h2 id="activity-title">Activity</h2>
                  <span>{data.events.length} events</span>
                </div>
                <ActivityTimeline events={events} sessionLabels={labels} />
              </aside>
            </div>

            <div className="sectionLine">
              <h2>Report</h2>
            </div>
            <ReportSection detail={data} />

            <div className="sectionLine">
              <h2>Defense configuration</h2>
            </div>
            <div className="policyRow">
              {policySet?.length ? (
                policySet.map((p) => (
                  <div className="policyChip" key={p.arm}>
                    <strong>{p.arm.toUpperCase()}</strong>
                    <span>{p.kind.replaceAll("_", " ")}</span>
                  </div>
                ))
              ) : (
                <Empty>No policies recorded.</Empty>
              )}
            </div>
            <div className="sectionLine">
              <h2>Evidence</h2>
            </div>
            <nav
              className="evidenceLinks"
              aria-label="Evidence for this experiment"
            >
              <Link to={`/results?experiment=${id}`}>
                Attack results <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <Link to={`/frontier?experiment=${id}`}>
                Leakage / cost frontier{" "}
                <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <Link to={`/reports?experiment=${id}`}>
                Report <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <Link to={`/traces?experiment=${id}`}>
                Trace explorer <ArrowRight size={18} aria-hidden="true" />
              </Link>
            </nav>
          </>
        )}
      </State>
    </>
  );
}
