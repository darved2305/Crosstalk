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
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../lib/client";
import { allJobs, displayState, pollInterval } from "../lib/pipeline";
import {
  buildExperimentConfig,
  policyCatalog,
  type ExperimentDraft,
} from "../lib/experiment-config";
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
    refetchInterval: (q) => pollInterval(q.state.data),
  });
  const sessions = details.data?.sessions ?? [];
  const jobs = details.data ? allJobs(details.data) : [];
  const complete = jobs.filter((j) => j.state === "succeeded").length;
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
          <p className="cardSub">
            {typeof details.data?.config.kind === "string"
              ? `${details.data.config.kind.toUpperCase()} experiment`
              : "Experiment"}{" "}
            · {item.id}
          </p>
        </div>
        <Status value={details.data ? displayState(details.data) : item.state} />
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
        <Link
          className="reviewLink"
          to={`/experiments/${item.id}`}
          aria-label={`Open ${item.name}: ${failed} job(s) need attention`}
        >
          <span className="countChip">{failed}</span>{" "}
          {failed === 1 ? "Job needs attention" : "Jobs need attention"}
          <ArrowRight size={20} aria-hidden="true" />
        </Link>
      </div>
      {details.error && (
        <p className="inlineError">{errorMessage(details.error)}</p>
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
  const [params, setParams] = useSearchParams();
  const status = params.get("state") ?? "all";
  const search = params.get("q") ?? "";
  const view = params.get("view") === "list" ? "list" : "grid";
  const [filterOpen, setFilterOpen] = useState(
    () => status !== "all" || search !== "",
  );
  const [cursor, setCursor] = useState<string | null>(null);
  const update = (key: string, value: string, fallback: string) => {
    const next = new URLSearchParams(params);
    if (value === fallback) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };
  const query = useQuery({
    queryKey: ["experiments", cursor],
    queryFn: () =>
      api(
        `/v1/experiments?limit=50${cursor ? `&cursor=${cursor}` : ""}`,
        experimentPage,
      ),
    refetchInterval: (q) =>
      q.state.data?.items.some((i) => i.state === "running") ? 5_000 : false,
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
          <span aria-live="polite">
            {query.data
              ? items.length === query.data.items.length
                ? `${query.data.items.length} total`
                : `${items.length} of ${query.data.items.length} shown`
              : "…"}
          </span>
        </div>
        <div className="toolbarActions">
          <button
            className="outlineButton"
            onClick={() => setFilterOpen(!filterOpen)}
            aria-expanded={filterOpen}
            aria-controls="experiment-filters"
          >
            <Filter size={18} /> Filters
          </button>
          <div className="viewSwitch" role="group" aria-label="View">
            <button
              aria-label="Grid view"
              aria-pressed={view === "grid"}
              className={view === "grid" ? "selected" : ""}
              onClick={() => update("view", "grid", "grid")}
            >
              <LayoutGrid size={18} />
            </button>
            <button
              aria-label="List view"
              aria-pressed={view === "list"}
              className={view === "list" ? "selected" : ""}
              onClick={() => update("view", "list", "grid")}
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
        <div className="filterPanel" id="experiment-filters" role="search">
          <label>
            <Search size={16} /> Search
            <input
              value={search}
              onChange={(e) => update("q", e.target.value, "")}
              type="search"
              placeholder="Name or experiment ID"
            />
          </label>
          <label>
            Status
            <select
              value={status}
              onChange={(e) => update("state", e.target.value, "all")}
            >
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
      <State
        loading={query.isLoading}
        error={query.data ? null : query.error}
        loadingMessage="Loading experiments…"
      >
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

export function NewExperiment() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [draft, setDraft] = useState<ExperimentDraft>({
    kind: "validation",
    name: "",
    requests: 20,
    perClass: 6,
    sessions: 2,
    concurrency: 1,
    seed: 1,
    arms: ["a0"],
  });
  const set = <K extends keyof ExperimentDraft>(
    key: K,
    value: ExperimentDraft[K],
  ) => setDraft((current) => ({ ...current, [key]: value }));
  const create = useMutation({
    mutationFn: () =>
      api("/v1/experiments", experimentCreated, {
        method: "POST",
        body: JSON.stringify(buildExperimentConfig(draft)),
      }),
    onSuccess: (data) => {
      void client.invalidateQueries({ queryKey: ["experiments"] });
      navigate(`/experiments/${data.id}`);
    },
  });
  const number =
    (key: "requests" | "perClass" | "sessions" | "concurrency" | "seed") =>
    (e: React.ChangeEvent<HTMLInputElement>) =>
      set(key, Number(e.target.value));
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">CONTROL PLANE</p>
        <h1>New experiment</h1>
        <p>
          Create an experiment configuration. Nothing is collected until you
          start a run from the experiment page.
        </p>
      </div>
      <form
        className="formPanel"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        aria-describedby="new-experiment-help"
      >
        <label>
          Experiment name
          <input
            value={draft.name}
            onChange={(e) => set("name", e.target.value)}
            required
            maxLength={120}
            placeholder="e.g. defense-wire-validation"
          />
        </label>
        <fieldset>
          <legend>Experiment type</legend>
          <div className="checkGrid">
            <label className="checkOption">
              <input
                type="radio"
                name="kind"
                checked={draft.kind === "validation"}
                onChange={() => set("kind", "validation")}
              />
              <span>
                Validation · synthetic requests
                <small>One session that checks the wire path.</small>
              </span>
            </label>
            <label className="checkOption">
              <input
                type="radio"
                name="kind"
                checked={draft.kind === "e0"}
                onChange={() => set("kind", "e0")}
              />
              <span>
                E0 calibration · corpus workload
                <small>
                  Block-randomized workloads/v1 sessions, quick analysis.
                </small>
              </span>
            </label>
          </div>
        </fieldset>
        <div className="formRow">
          {draft.kind === "validation" ? (
            <label>
              Requests
              <input
                type="number"
                min="1"
                max="10000"
                value={draft.requests}
                onChange={number("requests")}
                required
              />
            </label>
          ) : (
            <>
              <label>
                Requests per class
                <input
                  type="number"
                  min="1"
                  max="10000"
                  value={draft.perClass}
                  onChange={number("perClass")}
                  required
                />
              </label>
              <label>
                Sessions
                <input
                  type="number"
                  min="1"
                  max="8"
                  value={draft.sessions}
                  onChange={number("sessions")}
                  required
                />
              </label>
            </>
          )}
          <label>
            Concurrency
            <input
              type="number"
              min="1"
              max="256"
              value={draft.concurrency}
              onChange={number("concurrency")}
              required
            />
          </label>
          <label>
            {draft.kind === "e0" ? "First seed" : "Seed"}
            <input
              type="number"
              min="0"
              value={draft.seed}
              onChange={number("seed")}
              required
            />
          </label>
        </div>
        {draft.kind === "validation" && (
          <fieldset>
            <legend>Defenses</legend>
            <div className="checkGrid">
              {policyCatalog.map((p) => (
                <label className="checkOption" key={p.arm}>
                  <input
                    type="checkbox"
                    checked={draft.arms.includes(p.arm)}
                    onChange={(e) =>
                      set(
                        "arms",
                        e.target.checked
                          ? [...draft.arms, p.arm]
                          : draft.arms.filter((a) => a !== p.arm),
                      )
                    }
                  />
                  <span>{p.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <p className="helper" id="new-experiment-help">
          {draft.kind === "e0"
            ? `E0 creates ${draft.sessions} session(s) with seeds ${draft.seed}–${draft.seed + draft.sessions - 1} and a development analysis preset (chance + logistic regression, 100 bootstrap resamples). Results from a mock upstream lab are NOT REPORTABLE.`
            : "Validation creates one synthetic-request session. Full E1/E2 configurations can be submitted to the same API from the repository’s experiment YAML files."}
        </p>
        {create.error && (
          <p className="notice error" role="alert">
            Could not create experiment. {errorMessage(create.error)}
          </p>
        )}
        <button
          className="primary"
          disabled={
            create.isPending ||
            (draft.kind === "validation" && !draft.arms.length)
          }
          aria-busy={create.isPending}
        >
          {create.isPending ? "Creating…" : "Create experiment"}
        </button>
      </form>
    </>
  );
}
