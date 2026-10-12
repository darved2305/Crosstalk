export const formatDate = (date: string) =>
  new Date(date).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });

export const formatTime = (date: string) =>
  new Date(date).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

/** Shortens a ULID for dense UI while keeping the unique random tail visible. */
export const shortId = (id: string) => `${id.slice(0, 4)}…${id.slice(-6)}`;
