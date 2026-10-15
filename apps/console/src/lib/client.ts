import type { ZodType } from "zod";

const tokenKey = "crosstalk.operatorToken";
const oracleKey = "crosstalk.oracleToken";

/** An RFC 9457 problem returned by the API, or a transport failure. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly title?: string,
    readonly instance?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const credentials = {
  get: (oracle = false) =>
    sessionStorage.getItem(oracle ? oracleKey : tokenKey) ?? "",
  set: (token: string, oracle = false) => {
    sessionStorage.setItem(oracle ? oracleKey : tokenKey, token.trim());
    window.dispatchEvent(new Event("crosstalk:credentials"));
  },
  clear: () => {
    sessionStorage.removeItem(tokenKey);
    sessionStorage.removeItem(oracleKey);
    window.dispatchEvent(new Event("crosstalk:credentials"));
  },
};

type Problem = {
  title?: unknown;
  detail?: unknown;
  status?: unknown;
  instance?: unknown;
};

function describeDetail(detail: unknown): string | undefined {
  if (typeof detail === "string" && detail.trim()) return detail;
  // FastAPI's default validation shape: [{loc: [...], msg: "..."}]
  if (Array.isArray(detail))
    return detail
      .map((item: { loc?: unknown[]; msg?: unknown }) =>
        [Array.isArray(item?.loc) ? item.loc.join(".") : "", item?.msg]
          .filter(Boolean)
          .join(": "),
      )
      .filter(Boolean)
      .join("; ");
  return undefined;
}

/** Builds an ApiError from a failed response, reading problem+json when present. */
export async function problemFrom(response: Response): Promise<ApiError> {
  const problem = (await response.json().catch(() => null)) as Problem | null;
  const title = typeof problem?.title === "string" ? problem.title : undefined;
  const detail = describeDetail(problem?.detail);
  const message =
    detail ??
    title ??
    `API request failed (${response.status}${response.statusText ? ` ${response.statusText}` : ""})`;
  return new ApiError(
    response.status,
    message,
    title,
    typeof problem?.instance === "string" ? problem.instance : undefined,
  );
}

/** Human readable one-liner for any thrown value, including the problem title. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError)
    return error.title && error.title !== error.message
      ? `${error.title} (${error.status}): ${error.message}`
      : `${error.message} (${error.status})`;
  if (error instanceof Error) return error.message;
  return "An unexpected error occurred.";
}

async function send(path: string, init: RequestInit, oracle: boolean) {
  const token = credentials.get(oracle);
  if (!token)
    throw new ApiError(
      401,
      oracle
        ? "Enter an oracle token to request validation data."
        : "Enter an operator token to connect to the API.",
    );
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        // Not `Authorization`: proxies in front of the console (Cloud Shell preview, IAP)
        // claim that header for their own login. nginx and the Vite proxy map it back.
        "X-Crosstalk-Authorization": `Bearer ${token}`,
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch {
    throw new ApiError(0, "The API is unreachable. Check that it is running.");
  }
  if (!response.ok) throw await problemFrom(response);
  return response;
}

export async function api<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
  oracle = false,
): Promise<T> {
  const response = await send(path, init, oracle);
  const body: unknown = await response.json();
  return schema.parse(body);
}

export async function apiText(path: string): Promise<string> {
  const response = await send(path, {}, false);
  return response.text();
}
