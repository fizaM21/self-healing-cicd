"use client";
import Link from "next/link";
import { useCallback } from "react";
import { api, Incident, MemoryEntry, Stats } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Badge } from "@/components/Badge";

const ago = (iso: string) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`;
};

export default function Dashboard() {
  const stats = usePolling(useCallback(() => api<Stats>("stats"), []));
  const incidents = usePolling(useCallback(() => api<Incident[]>("incidents?limit=50"), []));
  const memory = usePolling(useCallback(() => api<MemoryEntry[]>("memory"), []), 15000);
  const s = stats.data;
  const maxCat = Math.max(1, ...Object.values(s?.by_category ?? {}));
  const err = stats.error ?? incidents.error;

  return (
    <>
      {err && <div className="card err">Cannot reach the backend: {err}</div>}
      <div className="grid">
        <div className="card"><div className="muted">Total incidents</div><div className="big">{s?.total ?? "–"}</div></div>
        <div className="card"><div className="muted">Awaiting your approval</div>
          <div className="big" style={{ color: "var(--warn)" }}>{s?.by_status.awaiting_approval ?? 0}</div></div>
        <div className="card"><div className="muted">Recovery rate</div>
          <div className="big">{s?.recovery_rate == null ? "–" : `${Math.round(s.recovery_rate * 100)}%`}</div></div>
        <div className="card"><div className="muted">Failure patterns learned</div><div className="big">{s?.memory_entries ?? "–"}</div></div>
      </div>

      <div className="card">
        <h2>Incidents</h2>
        <table>
          <thead><tr><th>#</th><th>Repo</th><th>Status</th><th className="hide-sm">Category</th><th className="hide-sm">Root cause</th><th>When</th></tr></thead>
          <tbody>
            {incidents.data?.map((i) => (
              <tr key={i.id}>
                <td><Link href={`/incidents/${i.id}`}>#{i.id}</Link></td>
                <td>{i.repo}<div className="muted">run {i.run_id} · {i.branch}</div></td>
                <td><Badge s={i.status} /></td>
                <td className="hide-sm">{i.category ?? "–"}</td>
                <td className="hide-sm">{i.root_cause ?? "–"}</td>
                <td className="muted">{ago(i.updated_at)}</td>
              </tr>
            ))}
            {incidents.data?.length === 0 && <tr><td colSpan={6} className="muted">No incidents yet. Failed GitHub Actions runs will show up here.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))" }}>
        <div className="card">
          <h2>Failures by category</h2>
          {Object.entries(s?.by_category ?? {}).map(([k, v]) => (
            <div className="bar" key={k}><span style={{ width: 110 }}>{k}</span>
              <i style={{ width: `${(v / maxCat) * 100}%`, minWidth: 6 }} /><span className="muted">{v}</span></div>
          ))}
          {!Object.keys(s?.by_category ?? {}).length && <span className="muted">No data yet.</span>}
        </div>
        <div className="card">
          <h2>Failure memory</h2>
          <table><tbody>
            {memory.data?.slice(0, 6).map((m) => (
              <tr key={m.signature}><td>{m.root_cause || m.category}<div className="muted">{m.category}</div></td>
                <td style={{ whiteSpace: "nowrap" }}><span style={{ color: "var(--ok)" }}>✓{m.successes}</span> <span style={{ color: "var(--bad)" }}>✗{m.failures}</span></td></tr>
            ))}
          </tbody></table>
          {!memory.data?.length && <span className="muted">Nothing learned yet.</span>}
        </div>
      </div>
    </>
  );
}
