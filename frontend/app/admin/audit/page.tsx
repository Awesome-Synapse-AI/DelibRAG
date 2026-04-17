"use client";

import { useMemo, useState } from "react";

import ProtectedShell from "@/components/ProtectedShell";
import { ApiError, exportAuditCsv, getAuditByQuery, getAuditBySession } from "@/lib/api";
import type { AuditEntry } from "@/lib/types";

export default function AuditViewerPage() {
  const [sessionId, setSessionId] = useState("");
  const [queryId, setQueryId] = useState("");
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [singleEntry, setSingleEntry] = useState<AuditEntry | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const sortedEntries = useMemo(
    () =>
      [...entries].sort((a, b) => {
        const aTime = a.timestamp ? new Date(a.timestamp).getTime() : 0;
        const bTime = b.timestamp ? new Date(b.timestamp).getTime() : 0;
        return bTime - aTime;
      }),
    [entries],
  );

  async function loadSessionAudit() {
    if (!sessionId.trim()) return;
    setLoading(true);
    setError(null);
    setSingleEntry(null);
    try {
      const data = await getAuditBySession(sessionId.trim());
      setEntries(data);
      if (data.length === 0) {
        setError("No audit entries found for this session. Note: Only high-stakes queries create audit entries.");
      }
    } catch (err) {
      setEntries([]);
      setError(err instanceof ApiError ? err.message : "Cannot load session audit");
    } finally {
      setLoading(false);
    }
  }

  async function loadQueryAudit() {
    if (!queryId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getAuditByQuery(queryId.trim());
      setSingleEntry(data);
      setEntries([]);
    } catch (err) {
      setSingleEntry(null);
      setError(err instanceof ApiError ? err.message : "Cannot load query audit");
    } finally {
      setLoading(false);
    }
  }

  async function downloadCsv() {
    setError(null);
    try {
      const blob = await exportAuditCsv();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "audit_export.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Export failed");
    }
  }

  return (
    <ProtectedShell title="Audit Trail Viewer" allowedRoles={["manager", "admin"]}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, height: "100%", overflow: "hidden" }}>
        <div className="card" style={{ padding: 14, flexShrink: 0 }}>
          <div className="grid-two">
            <div style={{ display: "grid", gap: 8 }}>
              <div className="heading" style={{ fontWeight: 700 }}>
                Browse by Session
              </div>
              <input className="input" placeholder="session_id" value={sessionId} onChange={(e) => setSessionId(e.target.value)} />
              <button className="btn btn-primary" onClick={() => void loadSessionAudit()} disabled={loading}>
                Load session entries
              </button>
            </div>
            <div style={{ display: "grid", gap: 8 }}>
              <div className="heading" style={{ fontWeight: 700 }}>
                Lookup by Query
              </div>
              <input className="input" placeholder="query_id" value={queryId} onChange={(e) => setQueryId(e.target.value)} />
              <button className="btn" onClick={() => void loadQueryAudit()} disabled={loading}>
                Load single entry
              </button>
              <button className="btn" onClick={() => void downloadCsv()}>
                Export CSV
              </button>
            </div>
          </div>
          {error && (
            <div className="banner banner-warning" style={{ marginTop: 10 }}>
              {error}
            </div>
          )}
        </div>

      {singleEntry && (
        <section className="card" style={{ padding: 14, flexShrink: 0 }}>
          <div className="heading" style={{ fontWeight: 700, marginBottom: 10 }}>
            Query Audit Detail
          </div>
          <pre style={{ margin: 0, overflowX: "auto", fontSize: 12 }}>{JSON.stringify(singleEntry, null, 2)}</pre>
        </section>
      )}

      <section className="card" style={{ padding: 14, flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div className="heading" style={{ fontWeight: 700, marginBottom: 10, flexShrink: 0 }}>
          Session Audit Log
        </div>
        {sortedEntries.length === 0 && !singleEntry && !loading && (
          <div className="muted">
            No entries loaded. Enter a session ID above to load audit entries.
            <br />
            <small>Note: Only high-stakes queries create audit entries.</small>
          </div>
        )}
        {loading && <div className="muted">Loading...</div>}
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          <div style={{ display: "grid", gap: 10 }}>
          {sortedEntries.map((entry) => {
            const isOpen = expanded[entry.query_id] ?? false;
            const retrievalPath = entry.retrieval_path ?? {};
            const evidence = entry.evidence_weighed ?? [];
            const contradictions = entry.contradictions_found ?? [];
            return (
              <article key={entry.query_id} className="card" style={{ padding: 12, background: "#f8fafc" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                  <div>
                    <strong>{entry.query_id}</strong>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {entry.timestamp ? new Date(entry.timestamp).toLocaleString() : "-"}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <span className={`badge ${entry.stakes_classification?.stakes_level === "high" ? "badge-high" : "badge-low"}`}>
                      {(entry.stakes_classification?.stakes_level ?? "low").toUpperCase()}
                    </span>
                    <button
                      className="btn"
                      onClick={() => setExpanded((prev) => ({ ...prev, [entry.query_id]: !isOpen }))}
                    >
                      {isOpen ? "Collapse" : "Expand"}
                    </button>
                  </div>
                </div>
                {isOpen && (
                  <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
                    <div>
                      <strong>Confidence:</strong> {Math.round((entry.confidence ?? 0) * 100)}% | Gate passed:{" "}
                      {String(entry.confidence_gate_passed)}
                    </div>
                    <div>
                      <strong>Human review:</strong> {String(entry.requires_human_review)}
                    </div>
                    <div>
                      <strong>Retrieval path:</strong>
                      <pre style={{ margin: "6px 0 0 0", fontSize: 12, overflowX: "auto" }}>{JSON.stringify(retrievalPath, null, 2)}</pre>
                    </div>
                    <div>
                      <strong>Evidence weighed ({evidence.length}):</strong>
                      <ul style={{ marginTop: 6, marginBottom: 0 }}>
                        {evidence.map((item) => (
                          <li key={item} style={{ fontSize: 13 }}>
                            {item}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <strong>Contradictions found ({contradictions.length})</strong>
                    </div>
                    <div>
                      <strong>Final answer:</strong>
                      <div style={{ marginTop: 4, whiteSpace: "pre-wrap" }}>{entry.final_answer ?? "-"}</div>
                    </div>
                  </div>
                )}
              </article>
            );
          })}
          </div>
        </div>
      </section>
      </div>
    </ProtectedShell>
  );
}
