import { z } from "zod";

export const ulid = z.string().regex(/^[0-7][0-9A-HJKMNP-TV-Z]{25}$/);

const job = z.object({
  id: ulid,
  kind: z.string(),
  queue: z.string(),
  state: z.string(),
  attempts: z.number().int(),
  max_attempts: z.number().int(),
  error: z.record(z.string(), z.unknown()).nullable().optional(),
});

export const session = z.object({
  id: ulid,
  experiment_id: ulid,
  state: z.string(),
  cancel_requested: z.boolean(),
  jobs: z.array(job),
});

export const experimentSummary = z.object({
  id: ulid,
  name: z.string(),
  config_hash: z.string().length(64),
  state: z.enum(["running", "completed", "partial", "failed", "cancelled"]),
  created_at: z.iso.datetime(),
  updated_at: z.iso.datetime(),
});

export const experimentPage = z.object({
  items: z.array(experimentSummary),
  next_cursor: ulid.nullable().optional(),
});

export const experimentCreated = z.object({ id: ulid, name: z.string(), config_hash: z.string() });
export const runCreated = z.object({ experiment_id: ulid, session_ids: z.array(ulid), replayed: z.boolean() });
export const controlResult = z.object({ id: ulid, state: z.string() });

export const experimentDetail = experimentSummary.extend({
  config: z.record(z.string(), z.unknown()),
  sessions: z.array(session),
});

export const evaluationResults = z.object({
  experiment_id: ulid,
  rows: z.array(
    z.object({
      session_id: ulid.nullable(),
      experiment_id: ulid,
      regime: z.string(),
      concurrency: z.number().int(),
      task: z.string(),
      representation: z.string(),
      arm: z.string(),
      setting: z.string(),
      model: z.string(),
      metric: z.string(),
      value: z.number().nullable(),
      ci_lo: z.number().nullable(),
      ci_hi: z.number().nullable(),
      n: z.number().int(),
      n_families: z.number().int(),
      B: z.number().int(),
    }),
  ),
});

export const frontierResults = z.object({
  experiment_id: ulid,
  setting: z.enum(["static", "adaptive"]),
  cost: z.string(),
  rows: z.array(
    z.object({
      session_id: ulid.nullable(),
      experiment_id: ulid,
      concurrency: z.number().int(),
      condition: z.string(),
      setting: z.string(),
      model: z.string(),
      representation: z.string(),
      balanced_accuracy: z.number().nullable(),
      rho: z.number().nullable(),
      rho_lo: z.number().nullable(),
      rho_hi: z.number().nullable(),
      bandwidth_overhead_pct: z.number().nullable(),
      ttft_p95_ms: z.number().nullable(),
      itl_p99_ms: z.number().nullable(),
    }),
  ),
});

const tracePoint = z.object({
  t_start_ns: z.number().int(),
  t_end_ns: z.number().int(),
  min_value: z.number().int(),
  max_value: z.number().int(),
  count: z.number().int(),
});

export const traceResults = z.object({
  session_id: ulid,
  request_id: ulid,
  layer: z.enum(["records", "packets"]),
  source_points: z.number().int(),
  downsampled: z.boolean(),
  points: z.array(tracePoint),
});

export const emissionResults = z.object({
  session_id: ulid,
  request_id: ulid,
  oracle: z.literal(true),
  warning: z.string(),
  rows: z.array(
    z.object({
      seq: z.number().int(),
      t_emit_ns: z.number().int(),
      kind: z.string(),
      framed_len: z.number().int(),
      content_bytes: z.number().int(),
      pad_bytes: z.number().int(),
      n_events: z.number().int(),
      queue_delay_ns: z.number().int(),
      timer_lateness_ns: z.number().int(),
      arm: z.string(),
    }),
  ),
});

export type ExperimentPage = z.infer<typeof experimentPage>;
export type ExperimentSummary = z.infer<typeof experimentSummary>;
export type ExperimentDetail = z.infer<typeof experimentDetail>;
export type Session = z.infer<typeof session>;
export type TraceResults = z.infer<typeof traceResults>;
export type EvaluationResults = z.infer<typeof evaluationResults>;
export type FrontierResults = z.infer<typeof frontierResults>;
