import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Ban, Play, RotateCcw } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/client";
import {
  controlResult,
  experimentDetail,
  runCreated,
  ulid,
} from "../lib/schemas";
import { Empty, Segments, State, Status } from "./common";
import { formatDate } from "./format";

export function Experiment() {
  const { id = "" } = useParams();
  const valid = ulid.safeParse(id).success;
  const client = useQueryClient();
  const [message, setMessage] = useState("");
  const query = useQuery({
    queryKey: ["experiment", id],
    queryFn: () => api(`/v1/experiments/${id}`, experimentDetail),
    enabled: valid,
    refetchInterval: 15_000,
  });
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["experiment", id] });
    client.invalidateQueries({ queryKey: ["experiments"] });
  };
  const run = useMutation({
    mutationFn: () =>
      api(`/v1/experiments/${id}/runs`, runCreated, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      }),
    onSuccess: (data) => {
      setMessage(
        data.replayed
          ? "Existing run returned."
          : `${data.session_ids.length} session(s) enqueued.`,
      );
      refresh();
    },
  });
  const cancel = useMutation({
    mutationFn: () =>
      api(`/v1/experiments/${id}/cancel`, controlResult, { method: "POST" }),
    onSuccess: () => {
      setMessage("Cancellation requested.");
      refresh();
    },
  });
  const retry = useMutation({
    mutationFn: (jobId: string) =>
      api(`/v1/jobs/${jobId}/retry`, controlResult, { method: "POST" }),
    onSuccess: () => {
      setMessage("Job retry requested.");
      refresh();
    },
  });
  if (!valid)
    return <State error={new Error("Experiment ID must be a ULID.")} />;
  const data = query.data;
  const jobs = data?.sessions.flatMap((s) => s.jobs) ?? [];
  const completed = jobs.filter((j) =>
    ["succeeded", "completed"].includes(j.state),
  ).length;
  const policySet = data?.config.policy_set as
    { arm: string; kind: string }[] | undefined;
  return (
    <>
      <Link className="backLink" to="/">
        <ArrowLeft size={16} /> Experiments
      </Link>
      <State loading={query.isLoading} error={query.error}>
        {data && (
          <>
            <div className="detailHead">
              <div>
                <p className="eyebrow">EXPERIMENT / {id}</p>
                <h1>{data.name}</h1>
                <div className="detailMeta">
                  <Status value={data.state} />
                  <span>Created {formatDate(data.created_at)}</span>
                  <span>Config {data.config_hash.slice(0, 12)}</span>
                </div>
              </div>
              <div className="detailActions">
                <button
                  className="outlineButton"
                  onClick={() => run.mutate()}
                  disabled={run.isPending || data.state === "cancelled"}
                >
                  <Play size={16} /> Start run
                </button>
                <button
                  className="outlineButton danger"
                  onClick={() => {
                    if (
                      window.confirm(
                        "Cancel this experiment and its queued jobs?",
                      )
                    )
                      cancel.mutate();
                  }}
                  disabled={
                    cancel.isPending ||
                    ["completed", "cancelled"].includes(data.state)
                  }
                >
                  <Ban size={16} /> Cancel
                </button>
              </div>
            </div>
            {(message || run.error || cancel.error || retry.error) && (
              <div
                className={
                  run.error || cancel.error || retry.error
                    ? "notice error"
                    : "notice"
                }
              >
                {run.error?.message ||
                  cancel.error?.message ||
                  retry.error?.message ||
                  message}
              </div>
            )}
            <div className="summaryGrid">
              <div className="summaryBox">
                <span>Sessions</span>
                <strong>{data.sessions.length}</strong>
              </div>
              <div className="summaryBox">
                <span>Validated</span>
                <strong>
                  {data.sessions.filter((s) => s.state === "VALID").length}
                </strong>
              </div>
              <div className="summaryBox">
                <span>Jobs complete</span>
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
              <h2>Sessions & jobs</h2>
              <span>Live control plane state</span>
            </div>
            {!data.sessions.length ? (
              <Empty>
                No sessions yet. Start a run to enqueue collection and analysis.
              </Empty>
            ) : (
              data.sessions.map((session) => (
                <section className="sessionPanel" key={session.id}>
                  <div className="sessionTop">
                    <div>
                      <Link to={`/traces?session=${session.id}`}>
                        <strong>Session {session.id}</strong>
                      </Link>
                      <p>
                        {session.cancel_requested
                          ? "Cancellation requested"
                          : `${session.jobs.length} pipeline jobs`}
                      </p>
                    </div>
                    <Status value={session.state} />
                  </div>
                  <div className="stageGrid">
                    {session.jobs.length ? (
                      session.jobs.map((job) => (
                        <div className="stageItem" key={job.id}>
                          <div>
                            <strong>{job.kind}</strong>
                            <Status value={job.state} />
                          </div>
                          <small>
                            {job.queue} queue · attempt {job.attempts}/
                            {job.max_attempts}
                          </small>
                          {job.error && (
                            <p className="inlineError">
                              {JSON.stringify(job.error)}
                            </p>
                          )}
                          {["failed", "dead"].includes(job.state) && (
                            <button
                              className="textButton"
                              disabled={retry.isPending}
                              onClick={() => retry.mutate(job.id)}
                            >
                              <RotateCcw size={14} /> Retry
                            </button>
                          )}
                        </div>
                      ))
                    ) : (
                      <Empty>No jobs enqueued.</Empty>
                    )}
                  </div>
                </section>
              ))
            )}
            <div className="sectionLine">
              <h2>Evidence</h2>
            </div>
            <div className="evidenceLinks">
              <Link to={`/results?experiment=${id}`}>
                Attack results <ArrowRight size={18} />
              </Link>
              <Link to={`/frontier?experiment=${id}`}>
                Leakage / cost frontier <ArrowRight size={18} />
              </Link>
              <Link to={`/reports?experiment=${id}`}>
                Report <ArrowRight size={18} />
              </Link>
              <Link to="/traces">
                Trace explorer <ArrowRight size={18} />
              </Link>
            </div>
          </>
        )}
      </State>
    </>
  );
}
