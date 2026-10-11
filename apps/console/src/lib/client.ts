import type { ZodType } from "zod";

const tokenKey = "crosstalk.operatorToken";
const oracleKey = "crosstalk.oracleToken";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
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

export async function api<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
  oracle = false,
): Promise<T> {
  const token = credentials.get(oracle);
  if (!token)
    throw new ApiError(
      401,
      oracle
        ? "Enter an oracle token to request validation data."
        : "Enter an operator token to connect to the API.",
    );
  const response = await fetch(path, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new ApiError(
      response.status,
      problem?.detail ?? `API request failed (${response.status})`,
    );
  }
  const body: unknown = await response.json();
  return schema.parse(body);
}

export async function apiText(path: string): Promise<string> {
  const token = credentials.get();
  if (!token)
    throw new ApiError(401, "Enter an operator token to connect to the API.");
  const response = await fetch(path, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new ApiError(
      response.status,
      problem?.detail ?? `API request failed (${response.status})`,
    );
  }
  return response.text();
}
