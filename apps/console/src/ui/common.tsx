import type { ReactNode } from "react";
import { AlertCircle, LoaderCircle } from "lucide-react";
import { ApiError } from "../lib/client";

export function Status({ value }: { value: string }) {
  const tone = ["completed", "VALID", "succeeded"].includes(value)
    ? "good"
    : ["failed", "dead", "INVALID", "partial", "cancelled"].includes(value)
      ? "bad"
      : "warn";
  return (
    <span className={`statusBadge ${tone}`}>
      <i />
      {value.replaceAll("_", " ")}
    </span>
  );
}
export function State({
  loading,
  error,
  children,
  notFoundMessage,
}: {
  loading?: boolean;
  error?: unknown;
  children?: ReactNode;
  notFoundMessage?: string;
}) {
  if (loading)
    return (
      <div className="statePanel">
        <LoaderCircle className="spin" size={20} /> Loading evidence…
      </div>
    );
  if (
    error instanceof ApiError &&
    error.status === 404 &&
    notFoundMessage &&
    /artifact/i.test(error.message)
  )
    return <div className="emptyState">{notFoundMessage}</div>;
  if (error)
    return (
      <div className="statePanel error">
        <AlertCircle size={20} />
        <div>
          <strong>
            {error instanceof ApiError && error.status === 401
              ? "Unauthorized"
              : "Unable to load data"}
          </strong>
          <p>
            {error instanceof Error
              ? error.message
              : "An unexpected error occurred."}
          </p>
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
