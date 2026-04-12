"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { API_BASE_URL, ApiError, deleteSession, getSession, listSessions, postChat } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import type { ChatResponse, SessionDetail, SessionMessage, SessionSummary } from "@/lib/types";

type UiMessage = SessionMessage & {
  id: string;
  requires_human_review?: boolean;
  out_of_scope?: boolean;
  citation_details?: Array<{
    source: string;
    title: string;
    section: string;
    trust_score: number;
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
  detail?: { source: string; title: string; section: string; trust_score: number },
) {
  if (detail) {
    return {
      title: detail.title,
      section: detail.section,
      trust: String(detail.trust_score),
    };
  }
  const normalized = citation.replaceAll("\\", "/");
  const title = normalized.split("/").pop() || citation;
  const section = citation.includes("§") ? citation.split("§")[1]?.trim() : "N/A";
  return { title, section, trust: "N/A" };
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
  const [openCitation, setOpenCitation] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const list = await listSessions();
        setSessions(list);
        let sessionId = initialSessionId || list[0]?.session_id || makeSessionId();
        setSelectedSessionId(sessionId);
        router.replace(`/chat/${sessionId}`);
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

  const currentMessages = useMemo(
    () => messagesBySession[selectedSessionId] ?? [],
    [messagesBySession, selectedSessionId],
  );

  async function loadHistory(sessionId: string) {
    setLoadingHistory(true);
    setError(null);
    try {
      const detail: SessionDetail = await getSession(sessionId);
      setMessagesBySession((prev) => ({ ...prev, [sessionId]: toUiMessages(detail.messages ?? []) }));
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
      router.replace(`/chat/${sessionId}`);
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
        router.replace(`/chat/${nextId}`);
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
    <div className="card" style={{ display: "grid", gridTemplateColumns: "280px 1fr", minHeight: "75vh", overflow: "hidden" }}>
      <aside style={{ borderRight: "1px solid #e2e8f0", background: "#fffaf5", padding: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <div className="heading" style={{ fontWeight: 700 }}>
            Sessions
          </div>
          <button
            className="btn"
            onClick={() => {
              const id = makeSessionId();
              setSelectedSessionId(id);
              setMessagesBySession((prev) => ({ ...prev, [id]: [] }));
              router.replace(`/chat/${id}`);
            }}
          >
            New
          </button>
        </div>
        <div style={{ display: "grid", gap: 8 }}>
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
                onClick={() => {
                  setSelectedSessionId(session.session_id);
                  router.replace(`/chat/${session.session_id}`);
                  void loadHistory(session.session_id);
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 13 }}>{session.session_id.slice(0, 12)}</div>
                <div className="muted" style={{ fontSize: 12 }}>
                  {session.message_count} messages
                </div>
                <div className="muted" style={{ fontSize: 11 }}>
                  {session.last_active ? new Date(session.last_active).toLocaleString() : "No activity"}
                </div>
              </button>
              <button className="btn" style={{ width: "100%", marginTop: 6 }} onClick={() => void removeSession(session.session_id)}>
                Delete
              </button>
            </div>
          ))}
        </div>
      </aside>
      <section style={{ display: "grid", gridTemplateRows: "1fr auto", minHeight: 0 }}>
        <div style={{ padding: 16, overflowY: "auto", background: "#fcfdff" }}>
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
                        const expanded = openCitation === key;
                        const detail = message.citation_details?.[index];
                        const meta = citationMeta(citation, detail);
                        return (
                          <div key={key}>
                            <button
                              className="btn"
                              style={{ borderRadius: 999, padding: "6px 10px", fontSize: 12 }}
                              onClick={() => setOpenCitation(expanded ? null : key)}
                            >
                              {meta.title}
                            </button>
                            {expanded && (
                              <div className="card" style={{ marginTop: 6, padding: 10, width: 260 }}>
                                <div style={{ fontSize: 13 }}>
                                  <strong>Doc:</strong> {meta.title}
                                </div>
                                <div style={{ fontSize: 13 }}>
                                  <strong>Section:</strong> {meta.section}
                                </div>
                                <div style={{ fontSize: 13 }}>
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
          </div>
        </div>
        <form onSubmit={sendMessage} style={{ borderTop: "1px solid #e2e8f0", padding: 12, background: "white" }}>
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
  );
}
