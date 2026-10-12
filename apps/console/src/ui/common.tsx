import type { ReactNode } from "react";
import { AlertCircle, LoaderCircle } from "lucide-react";
import { ApiError, errorMessage } from "../lib/client";

const GOOD = ["completed", "VALID", "succeeded"];
const BAD = ["failed", "dead", "INVALID", "FAILED"];
const WARN = ["partial", "PARTIAL"];
const MUTED = ["cancelled", "CANCELLED", "pending"];
const ACTIVE = ["running", "COLLECTING", "RECONSTRUCTING", "VALIDATING", "ANALYZING"];

function statusTone(value: string) {
  if (GOOD.includes(value)) return "good";
  if (BAD.includes(value)) return "bad";
  if (WARN.includes(value)) return "warn";
  if (MUTED.includes(value)) return "muted";
  if (ACTIVE.includes(value)) return "active";
  return "info";
}

export function Status({ value, label }: { value: string; label?: string }) {
  const tone = statusTone(value);
  return (
    <span className={`statusBadge ${tone}`} data-state={value}>
      {tone === "active" ? (
        <LoaderCircle className="spin" size={12} aria-hidden="true" />
      ) : (
        <i aria-hidden="true" />
      )}
      {label ? <span className="srOnly">{label}: </span> : null}
      {value.replaceAll("_", " ")}
    </span>
  );
}
export function State({
  loading,
  error,
  children,
  notFoundMessage,
  loadingMessage,
}: {
  loading?: boolean;
  error?: unknown;
  children?: ReactNode;
  notFoundMessage?: string;
  loadingMessage?: string;
}) {
  if (loading)
    return (
      <div className="statePanel" role="status" aria-live="polite">
        <LoaderCircle className="spin" size={20} aria-hidden="true" />{" "}
        {loadingMessage ?? "Loading…"}
      </div>
    );
  if (
    error instanceof ApiError &&
    error.status === 404 &&
    notFoundMessage &&
    /artifact/i.test(error.message)
  )
    return (
      <div className="emptyState" role="status">
        {notFoundMessage}
      </div>
    );
  if (error)
    return (
      <div className="statePanel error" role="alert">
        <AlertCircle size={20} aria-hidden="true" />
        <div>
          <strong>
            {error instanceof ApiError && error.status === 401
              ? "Unauthorized"
              : error instanceof ApiError && error.status === 404
                ? "Not found"
                : "Unable to load data"}
          </strong>
          <p>{errorMessage(error)}</p>
        </div>
      </div>
    );
  return <>{children}</>;
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="emptyState">{children}</div>;
}
export function Segments({
  value,
  total = 20,
  tone = "green",
}: {
  value: number;
  total?: number;
  tone?: "green" | "yellow" | "red";
}) {
  const filled = Math.round(Math.min(1, Math.max(0, value)) * total);
  return (
    <div
      className={`segments ${tone}`}
      role="meter"
      aria-valuenow={Math.round(value * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Completion percentage"
    >
      {Array.from({ length: total }, (_, i) => (
        <span className={i < filled ? "filled" : ""} key={i} />
      ))}
    </div>
  );
}
