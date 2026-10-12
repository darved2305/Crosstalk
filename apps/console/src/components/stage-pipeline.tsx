import { RotateCcw } from "lucide-react";
import { formatJobError, isRetryable, stageViews } from "../lib/pipeline";
import type { Job } from "../lib/schemas";
import { Status } from "../ui/common";
import { formatTime } from "../ui/format";

export function StagePipeline({
  label,
  jobs,
  stages,
  onRetry,
  retryingId,
  retryDisabled,
}: {
  /** Accessible name of the pipeline, e.g. "Session 01… stages". */
  label: string;
  jobs: readonly Job[];
  stages: readonly string[];
  onRetry?: (job: Job) => void;
  retryingId?: string | null;
  retryDisabled?: boolean;
}) {
  const views = stageViews(jobs, stages);
  return (
    <ol className="pipeline" aria-label={label}>
      {views.map(({ stage, job, state, expected }) => {
        const error = job ? formatJobError(job.error) : null;
        const retrying = Boolean(job && retryingId === job.id);
        return (
          <li
            key={stage}
            className={`pipelineStep step-${state}${expected ? "" : " extra"}`}
            data-stage={stage}
            data-state={state}
          >
            <div className="stepHead">
              <strong>{stage.replaceAll("_", " ")}</strong>
              <Status value={state} label={`${stage} stage`} />
            </div>
            {job ? (
              <small>
                {job.queue} queue · attempt {job.attempts}/{job.max_attempts}
                {job.updated_at ? ` · ${formatTime(job.updated_at)}` : ""}
              </small>
            ) : (
              <small>Not enqueued yet</small>
            )}
            {error && (
              <p className="stepError" role="note">
                {error}
              </p>
            )}
            {job && onRetry && isRetryable(job) && (
              <button
                type="button"
                className="textButton"
                disabled={retryDisabled || retrying}
                aria-busy={retrying}
                onClick={() => onRetry(job)}
              >
                <RotateCcw size={14} aria-hidden="true" />
                {retrying ? "Retrying…" : `Retry ${stage}`}
              </button>
            )}
          </li>
        );
      })}
    </ol>
  );
}
