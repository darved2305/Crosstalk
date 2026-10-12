export const policyCatalog = [
  { arm: "a0", kind: "passthrough", name: "D0 · Passthrough", parameters: {} },
  {
    arm: "a1",
    kind: "bucket_pad",
    name: "D1 · Bucket padding",
    parameters: { first_bucket: 256 },
  },
  {
    arm: "a2",
    kind: "window_batch",
    name: "D2 · Window batching",
    parameters: { window_ms: 100, max_merge_bytes: 8192 },
  },
  {
    arm: "a3",
    kind: "constant_rate",
    name: "D3 · Constant rate",
    parameters: { interval_ms: 50, record_size: 512, tail_quantum_ticks: 32 },
  },
  {
    arm: "a4",
    kind: "hybrid",
    name: "D4 · Hybrid",
    parameters: { window_ms: 100, max_merge_bytes: 8192, first_bucket: 256 },
  },
] as const;

/** Development analysis preset that finishes in about a minute on the local lab. */
const quickAnalysis = {
  models: ["chance", "lr"],
  null_models: ["lr"],
  seeds: 1,
  bootstrap: 100,
  null_permutations: 0,
  null_bootstrap: 100,
  learning_curve: false,
  primary_model: "lr",
};

export type ExperimentDraft = {
  kind: "validation" | "e0";
  name: string;
  requests: number;
  perClass: number;
  sessions: number;
  concurrency: number;
  seed: number;
  arms: string[];
};

/** Builds the ExperimentConfig body accepted by POST /v1/experiments. */
export function buildExperimentConfig(draft: ExperimentDraft) {
  const name = draft.name.trim();
  const policy_set = policyCatalog
    .filter((p) => draft.arms.includes(p.arm))
    .map(({ arm, kind, parameters }) => ({ arm, kind, parameters }));
  if (draft.kind === "validation")
    return {
      name,
      kind: "validation" as const,
      policy_set,
      sessions: [
        {
          experiment_id: name,
          requests: draft.requests,
          concurrency: draft.concurrency,
          seed: draft.seed,
          arms: draft.arms,
          validation: true,
        },
      ],
    };
  // E0 is undefended calibration: the API's default policy set (a0 passthrough) applies.
  return {
    name,
    kind: "e0" as const,
    sessions: Array.from({ length: draft.sessions }, (_, i) => ({
      experiment_id: name,
      workload: "workloads/v1",
      per_class: draft.perClass,
      concurrency: draft.concurrency,
      seed: draft.seed + i,
      validation: false,
      warmup: 0,
      determinism_every: 0,
    })),
    analysis: quickAnalysis,
  };
}
