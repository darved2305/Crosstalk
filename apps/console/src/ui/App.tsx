import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ChartNoAxesCombined,
  FileText,
  FlaskConical,
  LogOut,
  Network,
  PanelLeftClose,
  ShieldCheck,
} from "lucide-react";
import { credentials } from "../lib/client";
import { Dashboard, NewExperiment } from "./Dashboard";
import { Experiment } from "./Experiment";
import { Results, Frontier, Traces, Reports } from "./Evidence";

export function App() {
  const [token, setToken] = useState(() => credentials.get());
  const [draft, setDraft] = useState("");
  const queryClient = useQueryClient();
  const location = useLocation();
  useEffect(() => {
    const sync = () => setToken(credentials.get());
    window.addEventListener("crosstalk:credentials", sync);
    return () => window.removeEventListener("crosstalk:credentials", sync);
  }, []);
  const connect = (event: React.FormEvent) => {
    event.preventDefault();
    queryClient.clear();
    credentials.set(draft);
    setDraft("");
  };
  const nav = [
    ["/", "Experiments", FlaskConical],
    ["/traces", "Trace explorer", Activity],
    ["/results", "Attack results", ShieldCheck],
    ["/frontier", "Leakage / cost", ChartNoAxesCombined],
    ["/reports", "Reports", FileText],
  ] as const;
  return (
    <div className="appShell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brandMark">
            <Network size={22} />
          </span>
          <span>
            CROSSTALK<small>Research console</small>
          </span>
        </div>
        <div className="sideCaption">WORKSPACE</div>
        <nav aria-label="Primary navigation">
          {nav.map(([path, label, Icon]) => (
            <NavLink
              end={path === "/"}
              key={path}
              to={path}
              className={({ isActive }) =>
                `navLink ${isActive || (path === "/" && location.pathname.startsWith("/experiments")) ? "active" : ""}`
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebarFoot">
          <span className="liveDot" />{" "}
          {token ? "API credentials set" : "Not connected"}
        </div>
      </aside>
      <div className="mainColumn">
        <header className="topbar">
          <div className="mobileBrand">
            <Network size={19} /> Crosstalk
          </div>
          <div className="crumb">
            Research console <span>/</span>{" "}
            {nav.find(([path]) => path === location.pathname)?.[1] ??
              "Experiment"}
          </div>
          <div className="topActions">
            {token ? (
              <button
                className="quietButton"
                onClick={() => {
                  queryClient.clear();
                  credentials.clear();
                }}
              >
                <LogOut size={16} /> Disconnect
              </button>
            ) : (
              <PanelLeftClose size={18} aria-hidden="true" />
            )}
            <span className="avatar" aria-label="Operator">
              CT
            </span>
          </div>
        </header>
        <main className="content">
          {!token ? (
            <section className="connectCard">
              <div className="cardIcon">
                <Network size={23} />
              </div>
              <p className="eyebrow">CONNECT TO CROSSTALK</p>
              <h1>Research console</h1>
              <p>
                Enter an operator bearer token for the existing FastAPI service.
                It is kept only in this browser tab and is sent to same-origin{" "}
                <code>/v1</code> requests.
              </p>
              <form onSubmit={connect}>
                <label htmlFor="operator-token">Operator token</label>
                <input
                  id="operator-token"
                  type="password"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  autoComplete="off"
                  required
                  minLength={32}
                />
                <button className="primary" type="submit">
                  Connect
                </button>
              </form>
            </section>
          ) : (
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/new" element={<NewExperiment />} />
              <Route path="/experiments/:id" element={<Experiment />} />
              <Route path="/results" element={<Results />} />
              <Route path="/frontier" element={<Frontier />} />
              <Route path="/traces" element={<Traces />} />
              <Route path="/reports" element={<Reports />} />
              <Route
                path="*"
                element={
                  <div className="statePanel">This page does not exist.</div>
                }
              />
            </Routes>
          )}
        </main>
      </div>
    </div>
  );
}
