"use client";

import { useEffect, useMemo, useState } from "react";

import ProtectedShell from "@/components/ProtectedShell";
import {
  ApiError,
  assignGapTicket,
  deleteGapTicket,
  getGapTicket,
  listGapTickets,
  listUsers,
  resolveGapTicket,
} from "@/lib/api";
import type { GapTicket, UserSummary } from "@/lib/types";

function durationHours(from?: string | null, to?: string | null) {
  if (!from || !to) {
    return null;
  }
  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (Number.isNaN(ms)) {
    return null;
  }
  return ms / (1000 * 60 * 60);
}

export default function GapDashboardPage() {
  const [tickets, setTickets] = useState<GapTicket[]>([]);
  const [assignees, setAssignees] = useState<UserSummary[]>([]);
  const [statusFilter, setStatusFilter] = useState("open");
  const [deptFilter, setDeptFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [assignUserId, setAssignUserId] = useState<string>("");
  const [resolveAction, setResolveAction] = useState<"add_document" | "deprecate" | "update_document">("add_document");
  const [documentPath, setDocumentPath] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function loadAssignees() {
    try {
      const users = await listUsers(["manager", "admin"]);
      setAssignees(users);
      if (!assignUserId && users.length > 0) {
        setAssignUserId(users[0].id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Cannot load assignees");
    }
  }

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const data = await listGapTickets(statusFilter);
      setTickets(data);
      if (data.length > 0 && !selectedId) {
        setSelectedId(data[0].id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Cannot load tickets");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAssignees();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  const filtered = useMemo(() => {
    return tickets.filter((ticket) => {
      const depOk = deptFilter === "all" || (ticket.department ?? "unknown") === deptFilter;
      const typeOk = typeFilter === "all" || ticket.gap_type === typeFilter;
      return depOk && typeOk;
    });
  }, [tickets, deptFilter, typeFilter]);

  const selectedTicket = useMemo(() => filtered.find((ticket) => ticket.id === selectedId) ?? null, [filtered, selectedId]);

  const assigneeNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const u of assignees) {
      const label = (u.full_name || u.email || u.id).toString();
      map.set(u.id, label);
    }
    return map;
  }, [assignees]);

  const countsByDate = useMemo(() => {
    const map = new Map<string, number>();
    for (const ticket of filtered) {
      const date = ticket.created_at ? new Date(ticket.created_at).toISOString().slice(0, 10) : "unknown";
      map.set(date, (map.get(date) ?? 0) + 1);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filtered]);

  const resolutionBins = useMemo(() => {
    const bins: Record<string, number> = { "<24h": 0, "1-3d": 0, "3-7d": 0, ">7d": 0 };
    for (const ticket of filtered) {
      const hours = durationHours(ticket.created_at, ticket.resolved_at);
      if (hours == null) {
        continue;
      }
      if (hours < 24) bins["<24h"] += 1;
      else if (hours < 72) bins["1-3d"] += 1;
      else if (hours < 168) bins["3-7d"] += 1;
      else bins[">7d"] += 1;
    }
    return bins;
  }, [filtered]);

  const maxDateCount = Math.max(1, ...countsByDate.map((x) => x[1]));
  const maxBinCount = Math.max(1, ...Object.values(resolutionBins));

  return (
    <ProtectedShell title="Knowledge Gap Dashboard" allowedRoles={["manager", "admin"]}>
      <div className="grid-two">
        <section className="card" style={{ padding: 14 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
            <select className="select" style={{ maxWidth: 160 }} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="open">open</option>
              <option value="in_progress">in_progress</option>
              <option value="resolved">resolved</option>
              <option value="wont_fix">wont_fix</option>
            </select>
            <input
              className="input"
              style={{ maxWidth: 180 }}
              placeholder="department or all"
              value={deptFilter}
              onChange={(e) => setDeptFilter(e.target.value || "all")}
            />
            <select className="select" style={{ maxWidth: 180 }} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="all">all types</option>
              <option value="missing_knowledge">missing_knowledge</option>
              <option value="contradiction">contradiction</option>
              <option value="low_confidence">low_confidence</option>
            </select>
            <button className="btn" onClick={() => void loadData()}>
              Refresh
            </button>
          </div>
          {error && <div className="banner banner-warning">{error}</div>}
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", borderBottom: "1px solid #e2e8f0", padding: "8px 6px" }}>ID</th>
                  <th style={{ textAlign: "left", borderBottom: "1px solid #e2e8f0", padding: "8px 6px" }}>Type</th>
                  <th style={{ textAlign: "left", borderBottom: "1px solid #e2e8f0", padding: "8px 6px" }}>Dept</th>
                  <th style={{ textAlign: "left", borderBottom: "1px solid #e2e8f0", padding: "8px 6px" }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {!loading &&
                  filtered.map((ticket) => (
                    <tr key={ticket.id} style={{ cursor: "pointer" }} onClick={() => void setSelectedId(ticket.id)}>
                      <td style={{ borderBottom: "1px solid #f1f5f9", padding: "8px 6px" }}>{ticket.id.slice(0, 8)}</td>
                      <td style={{ borderBottom: "1px solid #f1f5f9", padding: "8px 6px" }}>{ticket.gap_type}</td>
                      <td style={{ borderBottom: "1px solid #f1f5f9", padding: "8px 6px" }}>{ticket.department ?? "-"}</td>
                      <td style={{ borderBottom: "1px solid #f1f5f9", padding: "8px 6px" }}>{ticket.status}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {!loading && filtered.length === 0 && <div className="muted" style={{ marginTop: 10 }}>No tickets found.</div>}
          </div>
        </section>

        <section className="card" style={{ padding: 14 }}>
          <div className="heading" style={{ fontWeight: 700, marginBottom: 8 }}>
            Ticket Detail
          </div>
          {!selectedTicket && <div className="muted">Select a ticket from the table.</div>}
          {selectedTicket && (
            <div style={{ display: "grid", gap: 10 }}>
              <div>
                <strong>Query:</strong> {selectedTicket.query}
              </div>
              <div>
                <strong>Description:</strong> {selectedTicket.description}
              </div>
              <div>
                <strong>Conflicting Sources:</strong> {(selectedTicket.conflicting_sources ?? []).join(", ") || "-"}
              </div>
              <div>
                <strong>Suggested Owner:</strong> {selectedTicket.suggested_owner ?? "-"}
              </div>
              <div>
                <strong>Assigned To:</strong>{" "}
                {selectedTicket.assigned_to_user_id
                  ? assigneeNameById.get(selectedTicket.assigned_to_user_id) ?? selectedTicket.assigned_to_user_id
                  : "-"}
              </div>

              <div className="card" style={{ padding: 10, background: "#f8fafc" }}>
                <div style={{ fontWeight: 600, marginBottom: 6 }}>Assign Ticket</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
                  <select className="select" value={assignUserId} onChange={(e) => setAssignUserId(e.target.value)}>
                    {assignees.map((u) => {
                      const primary = u.full_name || u.email || u.id;
                      const secondary = u.full_name ? u.email : null;
                      const labelParts = [primary, secondary].filter(Boolean);
                      const label = labelParts.join(" • ");
                      return (
                        <option key={u.id} value={u.id}>
                          {label}
                        </option>
                      );
                    })}
                    {assignees.length === 0 && <option value="">No assignees found</option>}
                  </select>
                  <button
                    className="btn"
                    onClick={async () => {
                      if (!assignUserId.trim()) return;
                      try {
                        await assignGapTicket(selectedTicket.id, assignUserId.trim());
                        await loadData();
                      } catch (err) {
                        setError(err instanceof ApiError ? err.message : "Assign failed");
                      }
                    }}
                    disabled={!assignUserId.trim()}
                  >
                    Assign
                  </button>
                </div>
              </div>

              <div className="card" style={{ padding: 10, background: "#f8fafc" }}>
                <div style={{ fontWeight: 600, marginBottom: 6 }}>Resolution</div>
                <div style={{ display: "grid", gap: 8 }}>
                  <select className="select" value={resolveAction} onChange={(e) => setResolveAction(e.target.value as typeof resolveAction)}>
                    <option value="add_document">add_document</option>
                    <option value="deprecate">deprecate</option>
                    <option value="update_document">update_document</option>
                  </select>
                  {(resolveAction === "add_document" || resolveAction === "update_document") && (
                    <input
                      className="input"
                      placeholder="document_path"
                      value={documentPath}
                      onChange={(e) => setDocumentPath(e.target.value)}
                    />
                  )}
                  {(resolveAction === "deprecate" || resolveAction === "update_document") && (
                    <input className="input" placeholder="source_id" value={sourceId} onChange={(e) => setSourceId(e.target.value)} />
                  )}
                  <textarea className="textarea" rows={3} placeholder="notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      className="btn btn-primary"
                      onClick={async () => {
                        try {
                          await resolveGapTicket(selectedTicket.id, {
                            action: resolveAction,
                            document_path: documentPath || undefined,
                            source_id: sourceId || undefined,
                            notes: notes || undefined,
                          });
                          await loadData();
                          const fresh = await getGapTicket(selectedTicket.id);
                          setSelectedId(fresh.id);
                        } catch (err) {
                          setError(err instanceof ApiError ? err.message : "Resolve failed");
                        }
                      }}
                    >
                      Resolve
                    </button>
                    <button
                      className="btn"
                      onClick={async () => {
                        try {
                          await deleteGapTicket(selectedTicket.id);
                          setSelectedId(null);
                          await loadData();
                        } catch (err) {
                          setError(err instanceof ApiError ? err.message : "Delete failed");
                        }
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </section>
      </div>

      <div className="grid-two" style={{ marginTop: 12 }}>
        <section className="card" style={{ padding: 14 }}>
          <div className="heading" style={{ fontWeight: 700, marginBottom: 8 }}>
            Gap Rate Over Time
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {countsByDate.map(([date, count]) => (
              <div key={date}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span>{date}</span>
                  <strong>{count}</strong>
                </div>
                <div style={{ height: 10, borderRadius: 8, background: "#e2e8f0", overflow: "hidden" }}>
                  <div style={{ width: `${(count / maxDateCount) * 100}%`, height: "100%", background: "#ea580c" }} />
                </div>
              </div>
            ))}
            {countsByDate.length === 0 && <div className="muted">No data.</div>}
          </div>
        </section>

        <section className="card" style={{ padding: 14 }}>
          <div className="heading" style={{ fontWeight: 700, marginBottom: 8 }}>
            Resolution Time Distribution
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {Object.entries(resolutionBins).map(([bucket, count]) => (
              <div key={bucket}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span>{bucket}</span>
                  <strong>{count}</strong>
                </div>
                <div style={{ height: 10, borderRadius: 8, background: "#e2e8f0", overflow: "hidden" }}>
                  <div style={{ width: `${(count / maxBinCount) * 100}%`, height: "100%", background: "#2563eb" }} />
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </ProtectedShell>
  );
}
