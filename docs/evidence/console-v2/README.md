# Console v2 screenshots (real local stack, 2026-10-12)

These images were captured by the opt-in Playwright spec
`apps/console/tests/pipeline-live.spec.ts` with `CT_E2E_SCREENSHOT_DIR` set to
this folder. The browser talked to the Vite dev server at
`http://127.0.0.1:3010`, which proxied `/v1` to the FastAPI service on
`127.0.0.1:18090` backed by native PostgreSQL (`127.0.0.1:55432`) and the
production `crosstalk-worker`. **No request was intercepted or mocked in the
browser.** The operator token was injected into `sessionStorage` before page
load; it is never typed into or rendered by the UI and does not appear in any
image.

**All data is development data and NOT REPORTABLE.** The worker ran the
`local-dev` lab: a synthetic mock upstream replaced the configured model, TLS
records were captured by a recording relay (synthetic TCP segmentation, no
dumpcap metadata), the tshark oracle was unavailable, and the source tree was
dirty. Every session therefore validated as `PARTIAL`, which the console shows
as a NOT REPORTABLE banner with these reasons.

## Experiments shown

| Experiment | ID | Source |
|---|---|---|
| `console-e2e-1791800755018` (E0, 2 sessions, 6 per class, concurrency 4, seeds 41/42, quick analysis preset) | `01M4QTBKTNGG53PM70B9FR008T` | Created and run through the UI by the spec; sessions `01M4QTBM76ARRETZBT2BBTABMM` and `01M4QTBM7B1Z3GX76C8Z0P8H9Q` |
| `e2-e2e` | `01M4QMV8R376ZEMK4S9XJ24X79` | Previously imported development campaign (`docs/evidence/campaign/campaign.json`); session `01M4QMQ5H6T34P9YRGWHY51ZQG`, request `01M4QMQ5GVD68Q2QTWAGBP34KN` |

## Images

| File | Viewport | What it shows |
|---|---|---|
| `01-experiment-list-desktop.png` | 1504×1128 | Experiment grid with per-experiment state, session/job counts and pipeline progress. `console-e2e-1791800221400` is shown as **Not Started** (the API reports a created-but-never-run experiment as `running`; the console relabels it). |
| `02-experiment-running-desktop.png` | 1504×1128 | `01M4QTBK…` seconds after **Start run**: "Run started: 2 session(s) enqueued", the live 2 s refresh indicator, Session 1 `collect` running, Session 2 `collect` queued, later stages pending ("Not enqueued yet"), analyze/report pending, and the first activity events. |
| `03-experiment-completed-desktop.png` | 1504×1128 | Same experiment after the worker finished, without a manual reload: polling stopped ("Pipeline idle · state partial"), NOT REPORTABLE banner with the validation reasons, every stage succeeded. |
| `03b-experiment-completed-desktop-full.png` | 1504×1128, full page | Full page: both session pipelines, experiment stages (analyze → report), the activity timeline (planned → collected → reconstructed → validated → analysis → report published), the inline rendered report preview and evidence links. |
| `04-report-desktop.png` | 1504×1128 | `report.md` for `01M4QTBK…` rendered as markdown (headings, blockquote, GFM tables, inline code). The report figure `figures/e0_balanced_accuracy.png` is shown as a captioned placeholder because the API does not serve report images. |
| `05-results-desktop.png` | 1504×1128 | Attack results table for `01M4QTBK…` (56 rows, point estimates with 95% cluster-bootstrap intervals) under the API's NOT REPORTABLE warning. |
| `06-frontier-desktop.png` | 1504×1128, full page | Leakage/cost view for `e2-e2e`, adaptive attacker, TTFT p95 axis. The retained artifact has `rho = null` for all 8 rows, so nothing is plotted; the chart says so and the table lists the measured balanced accuracy and costs with "Undefined" for ρ. |
| `07-trace-explorer-desktop.png` | 1504×1128, full page | Trace explorer for the `e2-e2e` request above: experiment and session chosen from selectors, request ID pasted, signed TLS-record bytes over time (server→client positive, client→server negative) with labelled axes and legend, plus the API's NOT REPORTABLE provenance warning. |
| `08-experiment-mobile.png` | 390×844 | Experiment `01M4QTBK…` on a phone viewport: header, actions and the NOT REPORTABLE banner; no horizontal page scroll (asserted by the spec). |
| `08b-experiment-mobile-full.png` | 390×844, full page | The same page in full: stacked pipelines, activity and report preview. |
| `09-experiment-list-mobile.png` | 390×844 | Experiment list on a phone viewport. |

## Reproduce

```powershell
# from apps/console, with the API, worker and Vite (port 3010) running
$env:CT_E2E_OPERATOR_TOKEN = "<operator token>"
$env:CT_E2E_BASE_URL = "http://127.0.0.1:3010"
$env:CT_E2E_SCREENSHOT_DIR = "..\..\docs\evidence\console-v2"
pnpm exec playwright test tests/pipeline-live.spec.ts
```

Each run creates and runs a new small E0 experiment, so IDs and timestamps
will differ from the images above.
