import "server-only";

import type { ZodType } from "zod";

const base = process.env.CT_CONSOLE_API_URL ?? "http://127.0.0.1:8080";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function apiGet<T>(
  path: string,
  schema: ZodType<T>,
  oracle = false,
): Promise<T> {
  const token = oracle
    ? process.env.CT_CONSOLE_ORACLE_TOKEN
    : process.env.CT_CONSOLE_OPERATOR_TOKEN;
  if (!token) throw new ApiError(503, "Console API token is not configured");
  const response = await fetch(new URL(path, base), {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(response.status, problem?.detail ?? `API request failed (${response.status})`);
  }
  return schema.parse(await response.json());
}
