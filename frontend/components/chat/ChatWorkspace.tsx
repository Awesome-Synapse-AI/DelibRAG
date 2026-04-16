"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { API_BASE_URL, ApiError, deleteSession, getSession, listSessions, postChat } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import type { ChatResponse, SessionDetail, SessionMessage, SessionSummary } from "@/lib/types";

type UiMessage = SessionMessage & {
  id: string;
  requires_human_review?: boolean;
  gap_ticket_id?: string | null;
  out_of_scope?: boolean;
  citation_details?: Array<{
    source: string;
    title: string;
    section: string;
    trust_score: number;
    excerpt?: string;
    content?: string;
    text?: string;
    snippet?: string;
  }>;
};

const OUT_OF_SCOPE_TEXT = "outside the current knowledge base scope";

function makeSessionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `sess-${Date.now()}`;
}

function toUiMessages(messages: SessionMessage[]): UiMessage[] {
  return messages.map((m, i) => ({
    ...m,
    id: `${m.role}-${i}-${Math.random().toString(16).slice(2)}`,
    out_of_scope: m.role === "assistant" && (m.content ?? "").toLowerCase().includes(OUT_OF_SCOPE_TEXT),
  }));
}

function citationMeta(
  citation: string,
  detail?: {
    source: string;
    title: string;
    section: string;
    trust_score: number;
    excerpt?: string;
    content?: string;
    text?: string;
    snippet?: string;
  },
) {
  const citationNormalized = citation.replaceAll("\\", "/");
  const fallbackTitle = citationNormalized.split("/").pop() || citation;
  const citationSection = citation.includes("§") ? citation.split("§")[1]?.trim() : "";

  const contentCandidates = [
    detail?.excerpt,
    detail?.content,
    detail?.text,
    detail?.snippet,
    detail?.section && detail.section !== "N/A" ? detail.section : "",
    citationSection,
  ];
  const content = contentCandidates.find((x) => typeof x === "string" && x.trim())?.trim() || "No excerpt available for this citation.";
  const trustValue = Number(detail?.trust_score);
  const trust = Number.isFinite(trustValue) ? String(trustValue) : "Unknown";

  if (detail) {
    return {
      title: (detail.title || fallbackTitle).trim(),
      content,
      trust,
    };
  }
  return { title: fallbackTitle, content, trust: "Unknown" };
}

async function streamChat(
  sessionId: string,
  query: string,
  onChunk: (chunk: string) => void,
): Promise<ChatResponse> {
  const token = getAccessToken();
  if (!token) {
    throw new Error("Missing access token");
  }
  const url = `${API_BASE_URL}/chat/stream?session_id=${encodeURIComponent(sessionId)}&query=${encodeURIComponent(query)}`;
  const response = await fetch(url, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!response.ok || !response.body) {
    const text = await response.text();
    throw new Error(text || "Cannot open stream");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let assembled = "";
  let finalPayload: ChatResponse | null = null;

  const consumeEvent = (rawEvent: string) => {
    const dataLines = rawEvent
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim());
    if (!dataLines.length) {
      return;
    }
    const data = dataLines.join("");
    if (!data) {
      return;
    }
    const parsed = JSON.parse(data) as {
      type?: "chunk" | "final";
      content?: string;
      answer?: string;
      citations?: string[];
      citation_details?: Array<{
        source: string;
        title: string;
        section: string;
        trust_score: number;
      }>;
      confidence?: number;
      stakes_level?: string;
      gap_ticket_id?: string | null;
      requires_human_review?: boolean;
      query_id?: string;
    };
    if (parsed.type === "chunk") {
      const chunk = parsed.content ?? "";
      assembled += chunk;
      onChunk(assembled);
      return;
    }
    if (parsed.type === "final") {
      if (parsed.answer) {
        assembled = parsed.answer;
        onChunk(assembled);
      }
      finalPayload = {
        answer: assembled,
        citations: parsed.citations ?? [],
        citation_details: parsed.citation_details ?? [],
        confidence: parsed.confidence ?? null,
        stakes_level: parsed.stakes_level,
        gap_ticket_id: parsed.gap_ticket_id,
        requires_human_review: parsed.requires_human_review,
        query_id: parsed.query_id,
      };
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const event = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      consumeEvent(event);
      boundary = buffer.indexOf("\n\n");
    }
  }

  return finalPayload ?? { answer: assembled, citations: [] };
}

interface ChatWorkspaceProps {
  initialSessionId?: string;
}

export default function ChatWorkspace({ initialSessionId }: ChatWorkspaceProps) {
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string>(initialSessionId ?? "");
  const [messagesBySession, setMessagesBySession] = useState<Record<string, UiMessage[]>>({});
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Open citation panel anchored above the chip (stable position so you can scroll inside). */
  const [citationPanel, setCitationPanel] = useState<{ key: string; x: number; y: number } | null>(null);
  const citationLeaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const citationPanelKeyRef = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  citationPanelKeyRef.current = citationPanel?.key ?? null;

  function clearCitationLeaveTimer() {
    if (citationLeaveTimerRef.current != null) {
      clearTimeout(citationLeaveTimerRef.current);
      citationLeaveTimerRef.current = null;
    }
  }

  function scheduleCitationClose() {
    clearCitationLeaveTimer();
    citationLeaveTimerRef.current = setTimeout(() => {
      setCitationPanel(null);
      citationLeaveTimerRef.current = null;
    }, 280);
  }

  useEffect(() => {
    return () => clearCitationLeaveTimer();
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const list = await listSessions();
        setSessions(list);
        let sessionId = initialSessionId || list[0]?.session_id || makeSessionId();
        setSelectedSessionId(sessionId);
        window.history.replaceState(null, "", `/chat/${sessionId}`);
        if (list.some((x) => x.session_id === sessionId)) {
          await loadHistory(sessionId);
        } else {
          setMessagesBySession((prev) => ({ ...prev, [sessionId]: [] }));
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Cannot load sessions");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!initialSessionId) {
      return;
    }
    setSelectedSessionId(initialSessionId);
    if (!messagesBySession[initialSessionId]) {
      void loadHistory(initialSessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSessionId]);

  function getSessionTitle(session: SessionSummary): string {
    return session.title || "New conversation";
  }

  function formatSessionDate(dateStr?: string | null): string {
    if (!dateStr) return "No activity";
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  }

  const currentMessages = useMemo(
    () => messagesBySession[selectedSessionId] ?? [],
    [messagesBySession, selectedSessionId],
  );

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [currentMessages]);

  async function loadHistory(sessionId: string) {
    setLoadingHistory(true);
    setError(null);
    try {
      const detail: SessionDetail = await getSession(sessionId);
      const messages = toUiMessages(detail.messages ?? []);
      setMessagesBySession((prev) => ({ ...prev, [sessionId]: messages }));
      
      // Update session title in the sessions list if it changed
      if (detail.title) {
        setSessions((prev) => 
          prev.map((s) => s.session_id === sessionId ? { ...s, title: detail.title } : s)
        );
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setMessagesBySession((prev) => ({ ...prev, [sessionId]: [] }));
        return;
      }
      setError(err instanceof ApiError ? err.message : "Cannot load session history");
    } finally {
      setLoadingHistory(false);
    }
  }

  function updateLatestAssistant(sessionId: string, updater: (prev: UiMessage) => UiMessage) {
    setMessagesBySession((prev) => {
      const list = [...(prev[sessionId] ?? [])];
      for (let i = list.length - 1; i >= 0; i -= 1) {
        if (list[i].role === "assistant") {
          list[i] = updater(list[i]);
          break;
        }
      }
      return { ...prev, [sessionId]: list };
    });
  }

  async function sendMessage(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = query.trim();
    if (!text || sending) {
      return;
    }
    setError(null);
    setQuery("");
    setSending(true);

    const sessionId = selectedSessionId || makeSessionId();
    if (!selectedSessionId) {
      setSelectedSessionId(sessionId);
      window.history.replaceState(null, "", `/chat/${sessionId}`);
    }

    const userMessage: UiMessage = {
      id: `u-${Date.now()}`,
      role: "user",
      content: text,
    };
    const assistantMessage: UiMessage = {
      id: `a-${Date.now()}`,
      role: "assistant",
      content: "",
      citations: [],
      citation_details: [],
      confidence: null,
      stakes_level: null,
      gap_ticket_id: null,
    };

    setMessagesBySession((prev) => ({
      ...prev,
      [sessionId]: [...(prev[sessionId] ?? []), userMessage, assistantMessage],
    }));

    try {
      let finalResponse = await streamChat(sessionId, text, (assembled) => {
        updateLatestAssistant(sessionId, (prev) => ({ ...prev, content: assembled }));
      });

      if (!finalResponse.answer) {
        finalResponse = await postChat({ session_id: sessionId, query: text });
      }

      updateLatestAssistant(sessionId, (prev) => ({
        ...prev,
        content: finalResponse.answer,
        citations: finalResponse.citations ?? [],
        citation_details: finalResponse.citation_details ?? [],
        confidence: finalResponse.confidence ?? null,
        stakes_level: finalResponse.stakes_level ?? null,
        gap_ticket_id: finalResponse.gap_ticket_id ?? null,
        requires_human_review: finalResponse.requires_human_review,
        out_of_scope: (finalResponse.answer ?? "").toLowerCase().includes(OUT_OF_SCOPE_TEXT),
      }));

      const nextSessions = await listSessions();
      setSessions(nextSessions);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Chat request failed");
      updateLatestAssistant(sessionId, (prev) => ({
        ...prev,
        content: "Request failed. Check backend logs and try again.",
      }));
    } finally {
      setSending(false);
    }
  }

  async function removeSession(sessionId: string) {
    try {
      await deleteSession(sessionId);
      const refreshed = await listSessions();
      setSessions(refreshed);
      setMessagesBySession((prev) => {
        const copy = { ...prev };
        delete copy[sessionId];
        return copy;
      });
      if (selectedSessionId === sessionId) {
        const nextId = refreshed[0]?.session_id || makeSessionId();
        setSelectedSessionId(nextId);
        window.history.replaceState(null, "", `/chat/${nextId}`);
        if (refreshed.some((x) => x.session_id === nextId)) {
          await loadHistory(nextId);
        } else {
          setMessagesBySession((prev) => ({ ...prev, [nextId]: [] }));
        }
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Cannot delete session");
    }
  }

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", flex: 1, minHeight: 0, overflow: "hidden" }}>
        <aside style={{ borderRight: "1px solid #e2e8f0", background: "#fffaf5", padding: 12, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, flexShrink: 0 }}>
            <div className="heading" style={{ fontWeight: 700 }}>
              Sessions
            </div>
            <button
              className="btn"
              onClick={() => {
                const id = makeSessionId();
                setSelectedSessionId(id);
                setMessagesBySession((prev) => ({ ...prev, [id]: [] }));
                window.history.replaceState(null, "", `/chat/${id}`);
              }}
            >
              New
            </button>
          </div>
          <div style={{ display: "grid", gap: 8, overflowY: "auto", flex: 1, minHeight: 0 }}>
          {sessions.map((session) => (
            <div
              key={session.session_id}
              style={{
                border: "1px solid #e2e8f0",
                borderRadius: 10,
                padding: 8,
                background: selectedSessionId === session.session_id ? "#ffedd5" : "#fff",
              }}
            >
              <button
                style={{ all: "unset", cursor: "pointer", display: "block", width: "100%" }}
                onClick={async (e) => {
                  e.preventDefault();
                  if (selectedSessionId === session.session_id) return;
                  setSelectedSessionId(session.session_id);
                  window.history.replaceState(null, "", `/chat/${session.session_id}`);
                  if (!messagesBySession[session.session_id]) {
                    await loadHistory(session.session_id);
                  }
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
                  {getSessionTitle(session)}
                </div>
                <div className="muted" style={{ fontSize: 11 }}>
                  {formatSessionDate(session.last_active)}
                </div>
              </button>
              <button className="btn" style={{ width: "100%", marginTop: 6 }} onClick={() => void removeSession(session.session_id)}>
                Delete
              </button>
            </div>
          ))}
        </div>
      </aside>
      <section style={{ display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
        <div style={{ padding: 16, overflowY: "auto", background: "#fcfdff", flex: 1, minHeight: 0 }}>
          {loadingHistory && <div className="muted">Loading history...</div>}
          {!loadingHistory && currentMessages.length === 0 && (
            <div className="muted">Ask anything from your indexed knowledge base.</div>
          )}
          <div style={{ display: "grid", gap: 12 }}>
            {currentMessages.map((message) => (
              <article
                key={message.id}
                className="card"
                style={{
                  padding: 12,
                  borderColor: message.role === "user" ? "#fdba74" : "#cbd5e1",
                  background: message.role === "user" ? "#fff7ed" : "white",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                  <strong style={{ textTransform: "capitalize" }}>{message.role}</strong>
                  {message.role === "assistant" && message.stakes_level && (
                    <span className={`badge ${message.stakes_level === "high" ? "badge-high" : "badge-low"}`}>
                      {message.stakes_level.toUpperCase()} stake
                    </span>
                  )}
                </div>

                {message.role === "assistant" && message.stakes_level === "high" && typeof message.confidence === "number" && (
                  <div style={{ marginTop: 8, fontSize: 13 }}>
                    Confidence: <strong>{Math.round(message.confidence * 100)}%</strong>
                  </div>
                )}

                {message.role === "assistant" && message.requires_human_review && (
                  <div className="banner banner-warning" style={{ marginTop: 8 }}>
                    Confidence below threshold. Human review is recommended.
                  </div>
                )}

                {message.role === "assistant" && message.gap_ticket_id && message.gap_ticket_id !== "pending" && (
                  <div className="banner banner-warning" style={{ marginTop: 8 }}>
                    Knowledge-gap ticket submitted: <strong>{message.gap_ticket_id.slice(0, 8)}</strong>
                  </div>
                )}

                {message.role === "assistant" && message.out_of_scope && (
                  <div className="banner banner-scope" style={{ marginTop: 8 }}>
                    Locked scope: This query is out of scope for current knowledge base.
                  </div>
                )}

                <div style={{ marginTop: 10, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{message.content}</div>

                {message.role === "assistant" && (message.citations?.length ?? 0) > 0 && (
                  <div style={{ marginTop: 10 }}>
                    <div className="muted" style={{ marginBottom: 6, fontSize: 13 }}>
                      Citations
                    </div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {(message.citations ?? []).map((citation, index) => {
                        const key = `${message.id}-${index}`;
                        const expanded = citationPanel?.key === key;
                        const detail = message.citation_details?.[index];
                        const meta = citationMeta(citation, detail);
                        const panelPos = citationPanel?.key === key ? citationPanel : null;
                        return (
                          <div
                            key={key}
                            style={{ position: "relative" }}
                            onMouseLeave={() => {
                              if (citationPanelKeyRef.current === key) {
                                scheduleCitationClose();
                              }
                            }}
                          >
                            <button
                              className="btn"
                              type="button"
                              style={{ borderRadius: 999, padding: "6px 10px", fontSize: 12 }}
                              onMouseEnter={(e) => {
                                clearCitationLeaveTimer();
                                const r = e.currentTarget.getBoundingClientRect();
                                setCitationPanel({
                                  key,
                                  x: r.left + r.width / 2,
                                  y: r.top - 6,
                                });
                              }}
                            >
                              {meta.title}
                            </button>
                            {expanded && panelPos && (
                              <div
                                className="card"
                                role="tooltip"
                                onMouseEnter={clearCitationLeaveTimer}
                                onMouseLeave={scheduleCitationClose}
                                style={{
                                  position: "fixed",
                                  left: panelPos.x,
                                  top: panelPos.y,
                                  transform: "translate(-50%, -100%)",
                                  zIndex: 1000,
                                  width: 480,
                                  maxHeight: 420,
                                  padding: 0,
                                  display: "flex",
                                  flexDirection: "column",
                                  overflow: "hidden",
                                  boxShadow: "0 10px 40px rgba(15, 23, 42, 0.18)",
                                }}
                              >
                                <div style={{ flexShrink: 0, padding: "10px 12px 6px", borderBottom: "1px solid #e2e8f0" }}>
                                  <div style={{ fontSize: 13, fontWeight: 600 }}>Doc</div>
                                  <div style={{ fontSize: 13, marginTop: 4, wordBreak: "break-word" }}>{meta.title}</div>
                                </div>
                                <div
                                  style={{
                                    flex: "1 1 auto",
                                    minHeight: 0,
                                    height: 300,
                                    overflowY: "auto",
                                    padding: "10px 12px",
                                    fontSize: 13,
                                    whiteSpace: "pre-wrap",
                                    lineHeight: 1.45,
                                  }}
                                >
                                  <strong>Content</strong>
                                  <div style={{ marginTop: 6 }}>{meta.content}</div>
                                </div>
                                <div
                                  style={{
                                    flexShrink: 0,
                                    padding: "8px 12px 10px",
                                    borderTop: "1px solid #e2e8f0",
                                    fontSize: 13,
                                    background: "#f8fafc",
                                  }}
                                >
                                  <strong>Trust score:</strong> {meta.trust}
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </article>
            ))}
            <div ref={messagesEndRef} />
          </div>
        </div>
        <form onSubmit={sendMessage} style={{ borderTop: "1px solid #e2e8f0", padding: 12, background: "white", flexShrink: 0 }}>
          {error && (
            <div className="banner banner-warning" style={{ marginBottom: 8 }}>
              {error}
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
            <textarea
              className="textarea"
              rows={3}
              placeholder="Type your question..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button className="btn btn-primary" type="submit" disabled={sending}>
              {sending ? "Sending..." : "Send"}
            </button>
          </div>
        </form>
      </section>
      </div>
    </div>
  );
}
