import { eventLabel, eventNotes, eventSummary } from "../lib/pipeline";
import type { PipelineEvent } from "../lib/schemas";
import { Status } from "../ui/common";
import { formatTime, shortId } from "../ui/format";

export function ActivityTimeline({
  events,
  sessionLabels,
  limit = 40,
}: {
  /** Most recent first, as returned by the API. */
  events: readonly PipelineEvent[];
  /** Optional friendly names for session IDs (e.g. "Session 1"). */
  sessionLabels?: ReadonlyMap<string, string>;
  limit?: number;
}) {
  if (!events.length)
    return (
      <div className="emptyState">
        No pipeline activity yet. Events appear as the worker plans, collects,
        reconstructs, validates, analyzes, and reports.
      </div>
    );
  const shown = events.slice(0, limit);
  return (
    <>
      <ol className="timeline" aria-label="Pipeline activity, most recent first">
        {shown.map((event) => {
          const summary = eventSummary(event);
          const notes = eventNotes(event);
          const status =
            event.kind === "session_validated" && typeof event.data.status === "string"
              ? event.data.status
              : null;
          const reasons =
            event.kind === "session_validated" && Array.isArray(event.data.reasons)
              ? event.data.reasons.filter((r): r is string => typeof r === "string")
              : [];
          return (
            <li key={event.id} className={`timelineItem kind-${event.kind}`}>
              <time dateTime={event.created_at} title={event.created_at}>
                {formatTime(event.created_at)}
              </time>
              <div className="timelineBody">
                <div className="timelineHead">
                  <strong>{eventLabel(event.kind)}</strong>
                  {status && <Status value={status} />}
                  <span className="timelineScope">
                    {event.session_id
                      ? (sessionLabels?.get(event.session_id) ??
                        `Session ${shortId(event.session_id)}`)
                      : "Experiment"}
                  </span>
                </div>
                {summary && <p>{summary}</p>}
                {(notes.length > 0 || reasons.length > 0) && (
                  <ul className="timelineNotes">
                    {[...reasons, ...notes].map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {events.length > shown.length && (
        <p className="helper">
          Showing the {shown.length} most recent of {events.length} events.
        </p>
      )}
    </>
  );
}
