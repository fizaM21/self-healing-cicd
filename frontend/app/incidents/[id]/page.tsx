"use client";
import { use, useCallback, useState } from "react";
import Link from "next/link";
import { api, IncidentDetail } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { Badge } from "@/components/Badge";

export default function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: inc, error, refresh } = usePolling(useCallback(() => api<IncidentDetail>(`incidents/${id}`), [id]), 3000);
  const [reviewer, setReviewer] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function decide(kind: "approve" | "reject") {
    setBusy(true); setActionError(null);
    try {
      await api(`incidents/${id}/${kind}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reviewer, comment }) });
      await refresh();
    } catch (e) { setActionError((e as Error).message); } finally { setBusy(false); }
  }

  if (error && !inc) return <div className="card err">{error}</div>;
  if (!inc) return <div className="muted">Loading…</div>;

  return (
    <>
      <p><Link href="/">← All incidents</Link></p>
      <div className="card">
        <div className="row"><h2 style={{ margin: 0 }}>Incident #{inc.id} · {inc.repo}</h2><Badge s={inc.status} /></div>
        <p className="muted">run {inc.run_id} · {inc.branch} · {inc.category ?? "unclassified"}
          {inc.confidence != null && ` · confidence ${Math.round(inc.confidence * 100)}%`}
          {inc.patch_source && ` · fix from ${inc.patch_source}`}</p>
        <p><b>Root cause:</b> {inc.root_cause ?? "Analysis in progress…"}</p>
        {inc.error && <p className="err">{inc.error}</p>}
        {inc.pr_url && <p>Pull request: <a href={inc.pr_url} target="_blank" rel="noreferrer noopener">{inc.pr_url}</a></p>}
      </div>

      {inc.patch && (
        <div className="card">
          <h2>Proposed patch</h2>
          {inc.patch.explanation && <p>{inc.patch.explanation}</p>}
          {inc.patch.edits.map((e, n) => (
            <div key={n} style={{ marginBottom: 12 }}>
              <code>{e.path}</code>
              {e.search && <div className="del">- {e.search}</div>}
              <div className="add">+ {e.replace}</div>
            </div>
          ))}
          {inc.status === "awaiting_approval" && (
            <>
              <div className="row">
                <input placeholder="Your name" value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
                <input placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
              </div>
              <div className="row" style={{ marginTop: 10 }}>
                <button className="approve" disabled={busy || !reviewer.trim()} onClick={() => decide("approve")}>Approve & test in sandbox</button>
                <button className="reject" disabled={busy || !reviewer.trim()} onClick={() => decide("reject")}>Reject</button>
              </div>
              {actionError && <p className="err">{actionError}</p>}
            </>
          )}
        </div>
      )}

      {inc.sandbox_result && (
        <div className="card">
          <h2>Sandbox run: {inc.sandbox_result.passed ? "✅ passed" : "❌ failed"}</h2>
          <p className="muted"><code>{inc.sandbox_result.command}</code></p>
          <pre>{inc.sandbox_result.output}</pre>
        </div>
      )}

      <div className="card"><h2>Failure log (secrets redacted)</h2><pre>{inc.logs || "(empty)"}</pre></div>

      <div className="card"><h2>Audit trail</h2>
        <ul className="tl">{inc.audit.map((a, n) => (
          <li key={n}><b>{a.action}</b> <span className="muted">by {a.actor} · {new Date(a.at).toLocaleString()}</span>
            {a.detail && <div className="muted">{a.detail}</div>}</li>))}</ul>
      </div>
    </>
  );
}
