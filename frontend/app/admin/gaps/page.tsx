"use client";

import { useEffect, useMemo, useState } from "react";

import ProtectedShell from "@/components/ProtectedShell";
import {
  ApiError,
  assignGapTicket,
  deleteGapTicket,
  getGapTicket,
  listIndexCollections,
  listSampleDocuments,
  listGapTickets,
  listUsers,
  resolveGapAddDocumentText,
  resolveGapAddDocumentUpload,
  resolveGapDeprecateSources,
  resolveGapUpdateDocumentUpload,
} from "@/lib/api";
import type { GapTicket, IndexCollectionOption, SampleDocumentEntry, UserSummary } from "@/lib/types";

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
  const [collections, setCollections] = useState<IndexCollectionOption[]>([]);
  const [sampleDocs, setSampleDocs] = useState<SampleDocumentEntry[]>([]);
  const [statusFilter, setStatusFilter] = useState("open");
  const [deptFilter, setDeptFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [assignUserId, setAssignUserId] = useState<string>("");
  const [resolveAction, setResolveAction] = useState<"add_document" | "deprecate" | "update_document">("add_document");
  const [targetDepartment, setTargetDepartment] = useState<string>("general");

  const [addMode, setAddMode] = useState<"upload" | "text">("upload");
  const [addFilename, setAddFilename] = useState<string>("");
  const [addText, setAddText] = useState<string>("");
  const [addFile, setAddFile] = useState<File | null>(null);

  const [updateTargetFilename, setUpdateTargetFilename] = useState<string>("");
  const [updateFile, setUpdateFile] = useState<File | null>(null);

  const [deprecateSources, setDeprecateSources] = useState<string[]>([]);
  const [manualSourceId, setManualSourceId] = useState<string>("");
  const [markDeprecated, setMarkDeprecated] = useState<boolean>(true);
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

  async function loadCollections() {
    try {
      const data = await listIndexCollections();
      setCollections(data);
      if (data.length > 0 && !targetDepartment) {
        setTargetDepartment(data[0].department);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Cannot load index collections");
    }
  }

  async function loadSampleDocs() {
    try {
      const docs = await listSampleDocuments();
      setSampleDocs(docs);
      if (!updateTargetFilename && docs.length > 0) {
        setUpdateTargetFilename(docs[0].name);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Cannot load sample documents");
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
    void loadCollections();
    void loadSampleDocs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  useEffect(() => {
    if (resolveAction !== "update_document") {
      return;
    }
    void loadSampleDocs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolveAction]);

  const filtered = useMemo(() => {
    return tickets.filter((ticket) => {
      const depOk = deptFilter === "all" || (ticket.department ?? "unknown") === deptFilter;
      const typeOk = typeFilter === "all" || ticket.gap_type === typeFilter;
      return depOk && typeOk;
    });
  }, [tickets, deptFilter, typeFilter]);

  const selectedTicket = useMemo(() => filtered.find((ticket) => ticket.id === selectedId) ?? null, [filtered, selectedId]);

  useEffect(() => {
    if (!selectedTicket || collections.length === 0) {
      return;
    }
    const dep = (selectedTicket.department ?? "general").toLowerCase();
    const normalized = dep === "manager" ? "management" : dep === "clinician" ? "clinical" : dep;
    if (collections.some((c) => c.department === normalized)) {
      setTargetDepartment(normalized);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTicket?.id, collections.length]);

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

                  <select className="select" value={targetDepartment} onChange={(e) => setTargetDepartment(e.target.value)}>
                    {collections.map((c) => (
                      <option key={c.department} value={c.department}>
                        {c.label}
                      </option>
                    ))}
                    {collections.length === 0 && <option value="general">General Index</option>}
                  </select>

                  {resolveAction === "add_document" && (
                    <div className="card" style={{ padding: 10, background: "white" }}>
                      <div style={{ fontWeight: 600, marginBottom: 6 }}>Add Document</div>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button className="btn" type="button" onClick={() => setAddMode("upload")} disabled={addMode === "upload"}>
                          Upload file
                        </button>
                        <button className="btn" type="button" onClick={() => setAddMode("text")} disabled={addMode === "text"}>
                          Paste text
                        </button>
                      </div>
                      {addMode === "upload" && (
                        <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                          <input
                            className="input"
                            placeholder="save as filename (optional, e.g. new_doc.txt)"
                            value={addFilename}
                            onChange={(e) => setAddFilename(e.target.value)}
                          />
                          <input
                            className="input"
                            type="file"
                            accept=".txt,.md,text/plain,text/markdown"
                            onChange={(e) => setAddFile(e.target.files?.[0] ?? null)}
                          />
                          <div className="muted" style={{ fontSize: 12 }}>
                            Saved to <code>sample-docs/</code> and indexed to the selected index.
                          </div>
                        </div>
                      )}
                      {addMode === "text" && (
                        <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
                          <input
                            className="input"
                            placeholder="filename (required, e.g. new_doc.txt)"
                            value={addFilename}
                            onChange={(e) => setAddFilename(e.target.value)}
                          />
                          <textarea className="textarea" rows={6} placeholder="document text..." value={addText} onChange={(e) => setAddText(e.target.value)} />
                        </div>
                      )}
                    </div>
                  )}

                  {resolveAction === "update_document" && (
                    <div className="card" style={{ padding: 10, background: "white" }}>
                      <div style={{ fontWeight: 600, marginBottom: 6 }}>Update Document</div>
                      <div style={{ display: "grid", gap: 8 }}>
                        <select className="select" value={updateTargetFilename} onChange={(e) => setUpdateTargetFilename(e.target.value)}>
                          {sampleDocs.map((d) => (
                            <option key={d.name} value={d.name}>
                              {d.name}
                            </option>
                          ))}
                          {sampleDocs.length === 0 && <option value="">No documents found</option>}
                        </select>
                        <input
                          className="input"
                          type="file"
                          accept=".txt,.md,text/plain,text/markdown"
                          onChange={(e) => setUpdateFile(e.target.files?.[0] ?? null)}
                        />
                        <div className="muted" style={{ fontSize: 12 }}>
                          Uploaded file overwrites the selected name in <code>sample-docs/</code> and is re-indexed.
                        </div>
                      </div>
                    </div>
                  )}

                  {resolveAction === "deprecate" && (
                    <div className="card" style={{ padding: 10, background: "white" }}>
                      <div style={{ fontWeight: 600, marginBottom: 6 }}>Deprecate Sources</div>
                      <select className="select" value={markDeprecated ? "yes" : "no"} onChange={(e) => setMarkDeprecated(e.target.value === "yes")}>
                        <option value="yes">Mark as deprecated</option>
                        <option value="no">Keep active (prevent future auto-deprecate)</option>
                      </select>
                      <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
                        {(selectedTicket.conflicting_sources ?? []).length > 0 ? (
                          (selectedTicket.conflicting_sources ?? []).map((src) => {
                            const checked = deprecateSources.includes(src);
                            return (
                              <label key={src} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={(e) => {
                                    setDeprecateSources((prev) => {
                                      if (e.target.checked) return [...new Set([...prev, src])];
                                      return prev.filter((x) => x !== src);
                                    });
                                  }}
                                />
                                <span style={{ wordBreak: "break-all" }}>{src}</span>
                              </label>
                            );
                          })
                        ) : (
                          <div className="muted" style={{ fontSize: 12 }}>
                            No conflicting sources on this ticket. Add a source id manually below.
                          </div>
                        )}
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8, marginTop: 8 }}>
                        <input
                          className="input"
                          placeholder="source id (e.g. sample-docs/xyz.txt)"
                          value={manualSourceId}
                          onChange={(e) => setManualSourceId(e.target.value)}
                        />
                        <button
                          className="btn"
                          type="button"
                          onClick={() => {
                            const v = manualSourceId.trim();
                            if (!v) return;
                            setDeprecateSources((prev) => [...new Set([...prev, v])]);
                            setManualSourceId("");
                          }}
                        >
                          Add
                        </button>
                      </div>
                      {deprecateSources.length > 0 && (
                        <div className="muted" style={{ fontSize: 12 }}>
                          Selected: {deprecateSources.length} source(s)
                        </div>
                      )}
                    </div>
                  )}

                  <textarea className="textarea" rows={3} placeholder="notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      className="btn btn-primary"
                      onClick={async () => {
                        try {
                          if (resolveAction === "add_document") {
                            if (addMode === "upload") {
                              if (!addFile) {
                                throw new Error("Please choose a file to upload");
                              }
                              await resolveGapAddDocumentUpload({
                                ticketId: selectedTicket.id,
                                file: addFile,
                                filename: addFilename.trim() || undefined,
                                target_department: targetDepartment,
                                notes: notes || undefined,
                              });
                            } else {
                              if (!addFilename.trim()) {
                                throw new Error("Filename is required");
                              }
                              await resolveGapAddDocumentText({
                                ticketId: selectedTicket.id,
                                filename: addFilename.trim(),
                                text: addText,
                                target_department: targetDepartment,
                                notes: notes || undefined,
                              });
                            }
                            await loadSampleDocs();
                          } else if (resolveAction === "update_document") {
                            if (!updateTargetFilename.trim()) {
                              throw new Error("Please select a document to update");
                            }
                            if (!updateFile) {
                              throw new Error("Please choose a file to upload");
                            }
                            await resolveGapUpdateDocumentUpload({
                              ticketId: selectedTicket.id,
                              target_filename: updateTargetFilename.trim(),
                              file: updateFile,
                              target_department: targetDepartment,
                              notes: notes || undefined,
                            });
                            await loadSampleDocs();
                          } else if (resolveAction === "deprecate") {
                            if (deprecateSources.length === 0) {
                              throw new Error("Select at least one source");
                            }
                            await resolveGapDeprecateSources({
                              ticketId: selectedTicket.id,
                              source_ids: deprecateSources,
                              is_deprecated: markDeprecated,
                              target_department: targetDepartment,
                              notes: notes || undefined,
                            });
                          }
                          await loadData();
                          const fresh = await getGapTicket(selectedTicket.id);
                          setSelectedId(fresh.id);
                        } catch (err) {
                          if (err instanceof ApiError) setError(err.message);
                          else if (err instanceof Error) setError(err.message);
                          else setError("Resolve failed");
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
