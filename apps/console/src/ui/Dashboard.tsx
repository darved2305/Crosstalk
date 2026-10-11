import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Filter,
  LayoutGrid,
  List,
  Plus,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../lib/client";
import {
  experimentCreated,
  experimentDetail,
  experimentPage,
  type ExperimentSummary,
} from "../lib/schemas";
import { Empty, Segments, State, Status } from "./common";
import { formatDate } from "./format";

function ExperimentCard({ item }: { item: ExperimentSummary }) {
  const details = useQuery({
    queryKey: ["experiment", item.id],
    queryFn: () => api(`/v1/experiments/${item.id}`, experimentDetail),
  });
  const sessions = details.data?.sessions ?? [];
  const jobs = sessions.flatMap((s) => s.jobs);
  const complete = jobs.filter(
    (j) => j.state === "succeeded" || j.state === "completed",
  ).length;
  const failed = jobs.filter(
    (j) => j.state === "failed" || j.state === "dead",
  ).length;
  const valid = sessions.filter((s) => s.state === "VALID").length;
  const progress = jobs.length ? complete / jobs.length : 0;
  return (
    <article className="experimentCard">
      <div className="cardTitle">
        <div>
          <h3>
            <Link to={`/experiments/${item.id}`}>{item.name}</Link>
          </h3>
          <p className="cardSub">Experiment · {item.id}</p>
        </div>
        <Status value={item.state} />
      </div>
      <div className="metricGrid">
        <div className="metricBox">
          <div className="metricLine">
            <span>Sessions</span>
            <strong>{details.isLoading ? "—" : sessions.length}</strong>
          </div>
          <div className="metricLine">
            <span>Validated</span>
            <strong>{details.isLoading ? "—" : valid}</strong>
          </div>
          <div className="metricLine">
            <span>Jobs completed</span>
            <strong>{details.isLoading ? "—" : complete}</strong>
          </div>
        </div>
        <div className="metricBox">
          <span className="metricLabel">Pipeline progress</span>
          <div className="metricValue">
            {details.isLoading ? "—" : `${Math.round(progress * 100)}%`}
            <small>
              {complete}/{jobs.length} jobs
            </small>
          </div>
          <Segments
            value={progress}
            tone={
              failed ? "red" : item.state === "completed" ? "green" : "yellow"
            }
          />
        </div>
      </div>
      <div className="cardActions">
        <span className="armBox">
          <SlidersHorizontal size={18} />
          {details.data
            ? (details.data.config.policy_set as { arm: string }[] | undefined)
                ?.map((p) => p.arm.toUpperCase())
                .join(" · ") || "No policy set"
            : "Loading policies"}
        </span>
        <Link className="reviewLink" to={`/experiments/${item.id}`}>
          <span className="countChip">{failed}</span>{" "}
          {failed === 1 ? "Job needs attention" : "Jobs need attention"}
          <ArrowRight size={20} />
        </Link>
      </div>
      {details.error && (
        <p className="inlineError">
          {details.error instanceof Error
            ? details.error.message
            : "Detail unavailable"}
        </p>
      )}
      <div className="cardFoot">
        <span>
          <i
            className={`liveDot ${item.state === "completed" ? "" : "amber"}`}
          />{" "}
          {item.state === "completed"
            ? "Experiment complete"
            : "Experiment " + item.state}
        </span>
        <span>
          Updated: <strong>{formatDate(item.updated_at)}</strong>
        </span>
      </div>
    </article>
  );
}

export function Dashboard() {
  const [filterOpen, setFilterOpen] = useState(false);
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [cursor, setCursor] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["experiments", cursor],
    queryFn: () =>
      api(
        `/v1/experiments?limit=50${cursor ? `&cursor=${cursor}` : ""}`,
        experimentPage,
      ),
  });
  const items = (query.data?.items ?? []).filter(
    (item) =>
      (status === "all" || item.state === status) &&
      `${item.name} ${item.id}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">OVERVIEW</p>
        <h1>Experiments</h1>
        <p>
          Track collection, defenses, analysis, and reportability across your
          research runs.
        </p>
      </div>
      <div className="toolbar">
        <div className="toolbarTitle">
          <strong>All experiments</strong>
          <span>{query.data?.items.length ?? 0} total</span>
        </div>
        <div className="toolbarActions">
          <button
            className="outlineButton"
            onClick={() => setFilterOpen(!filterOpen)}
            aria-expanded={filterOpen}
          >
            <Filter size={18} /> Filters
          </button>
          <div className="viewSwitch" role="group" aria-label="View">
            <button
              aria-label="Grid view"
              aria-pressed={view === "grid"}
              className={view === "grid" ? "selected" : ""}
              onClick={() => setView("grid")}
            >
              <LayoutGrid size={18} />
            </button>
            <button
              aria-label="List view"
              aria-pressed={view === "list"}
              className={view === "list" ? "selected" : ""}
              onClick={() => setView("list")}
            >
              <List size={18} />
            </button>
          </div>
          <Link className="primary" to="/new">
            <Plus size={20} /> New Experiment
          </Link>
        </div>
      </div>
      {filterOpen && (
        <div className="filterPanel">
          <label>
            <Search size={16} /> Search
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Name or experiment ID"
            />
          </label>
          <label>
            Status
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="all">All states</option>
              {["running", "completed", "partial", "failed", "cancelled"].map(
                (s) => (
                  <option key={s}>{s}</option>
                ),
              )}
            </select>
          </label>
        </div>
      )}
      <State loading={query.isLoading} error={query.error}>
        {query.data &&
          (items.length ? (
            view === "grid" ? (
              <div className="cardGrid">
                {items.map((item) => (
                  <ExperimentCard key={item.id} item={item} />
                ))}
              </div>
            ) : (
              <div className="tableWrap">
                <table>
                  <thead>
                    <tr>
                      <th>Experiment</th>
                      <th>Status</th>
                      <th>Created</th>
                      <th>Updated</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.id}>
                        <td>
                          <strong>{item.name}</strong>
                          <small>{item.id}</small>
                        </td>
                        <td>
                          <Status value={item.state} />
                        </td>
                        <td>{formatDate(item.created_at)}</td>
                        <td>{formatDate(item.updated_at)}</td>
                        <td>
                          <Link to={`/experiments/${item.id}`}>
                            Open <ArrowRight size={16} />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            <Empty>
              {query.data.items.length
                ? "No experiments match these filters."
                : "No experiments yet. Create one to begin."}
            </Empty>
          ))}
        {query.data?.next_cursor && (
          <button
            className="outlineButton loadMore"
            onClick={() => setCursor(query.data!.next_cursor!)}
          >
            Next page
          </button>
        )}
      </State>
      <p className="footnote">
        Only validated artifacts are eligible for research reporting.
        Measurements remain pending until experiments produce evidence.
      </p>
    </>
  );
}

const policyCatalog = [
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

export function NewExperiment() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [requests, setRequests] = useState(20);
  const [concurrency, setConcurrency] = useState(1);
  const [seed, setSeed] = useState(1);
  const [arms, setArms] = useState<string[]>(["a0"]);
  const create = useMutation({
    mutationFn: async () => {
      const payload = {
        name: name.trim(),
        policy_set: policyCatalog
          .filter((p) => arms.includes(p.arm))
          .map(({ arm, kind, parameters }) => ({ arm, kind, parameters })),
        sessions: [
          {
            experiment_id: name.trim(),
            requests,
            concurrency,
            seed,
            arms,
            validation: true,
          },
        ],
      };
      return api("/v1/experiments", experimentCreated, {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    onSuccess: (data) => {
      client.invalidateQueries({ queryKey: ["experiments"] });
      navigate(`/experiments/${data.id}`);
    },
  });
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">CONTROL PLANE</p>
        <h1>New experiment</h1>
        <p>
          Create a validation experiment with the selected D0–D4 defense arms.
          Collection begins when you start a run.
        </p>
      </div>
      <form
        className="formPanel"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <label>
          Experiment name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={120}
            placeholder="e.g. defense-wire-validation"
          />
        </label>
        <div className="formRow">
          <label>
            Requests
            <input
              type="number"
              min="1"
              max="10000"
              value={requests}
              onChange={(e) => setRequests(Number(e.target.value))}
              required
            />
          </label>
          <label>
            Concurrency
            <input
              type="number"
              min="1"
              max="256"
              value={concurrency}
              onChange={(e) => setConcurrency(Number(e.target.value))}
              required
            />
          </label>
          <label>
            Seed
            <input
              type="number"
              min="0"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
              required
            />
          </label>
        </div>
        <fieldset>
          <legend>Defenses</legend>
          <div className="checkGrid">
            {policyCatalog.map((p) => (
              <label className="checkOption" key={p.arm}>
                <input
                  type="checkbox"
                  checked={arms.includes(p.arm)}
                  onChange={(e) =>
                    setArms((current) =>
                      e.target.checked
                        ? [...current, p.arm]
                        : current.filter((a) => a !== p.arm),
                    )
                  }
                />
                <span>{p.name}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <p className="helper">
          This form creates a synthetic validation session. Corpus-driven E0–E2
          configurations can be submitted to the same API using the repository’s
          experiment YAML files.
        </p>
        {create.error && <p className="inlineError">{create.error.message}</p>}
        <button className="primary" disabled={create.isPending || !arms.length}>
          {create.isPending ? "Creating…" : "Create experiment"}
        </button>
      </form>
    </>
  );
}
