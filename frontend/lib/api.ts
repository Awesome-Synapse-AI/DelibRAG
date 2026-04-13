import { authHeader, clearTokens, getRefreshToken, refreshAuthHeader, setTokens } from "./auth";
import type {
  AuditEntry,
  AuthTokens,
  ChatResponse,
  CurrentUser,
  GapTicket,
  SessionDetail,
  SessionSummary,
} from "./types";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

let refreshPromise: Promise<AuthTokens | null> | null = null;

async function parseErrorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") {
      return payload.detail;
    }
    if (Array.isArray(payload?.detail)) {
      return payload.detail.map((x: { msg?: string }) => x.msg).filter(Boolean).join("; ");
    }
    return JSON.stringify(payload);
  } catch {
    return response.statusText || "Request failed";
  }
}

export async function apiRequest<T>(
  path: string,
  options?: RequestInit & { auth?: boolean },
): Promise<T> {
  const { auth = true, headers, ...rest } = options ?? {};
  const mergedHeaders: HeadersInit = {
    ...(auth ? authHeader() : {}),
    ...(headers ?? {}),
  };

  let response = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: mergedHeaders,
  });

  if (auth && response.status === 401) {
    const refreshed = await refreshTokens();
    if (refreshed?.access_token) {
      response = await fetch(`${API_BASE_URL}${path}`, {
        ...rest,
        headers: {
          ...(auth ? authHeader() : {}),
          ...(headers ?? {}),
        },
      });
    }
  }

  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function refreshTokens(): Promise<AuthTokens | null> {
  if (!getRefreshToken()) {
    return null;
  }
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: "POST",
        headers: { ...refreshAuthHeader() },
      });
      if (!response.ok) {
        clearTokens();
        return null;
      }
      const tokens = (await response.json()) as AuthTokens;
      setTokens(tokens);
      return tokens;
    })().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

export async function register(payload: {
  email: string;
  password: string;
  full_name?: string;
  role: string;
  department: string;
}): Promise<AuthTokens> {
  return apiRequest<AuthTokens>("/auth/register", {
    method: "POST",
    auth: false,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function login(payload: { email: string; password: string }): Promise<AuthTokens> {
  return apiRequest<AuthTokens>("/auth/login", {
    method: "POST",
    auth: false,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getMe(): Promise<CurrentUser> {
  return apiRequest<CurrentUser>("/auth/me");
}

export async function refreshToken(): Promise<AuthTokens | null> {
  return refreshTokens();
}

export async function postChat(payload: { session_id: string; query: string }): Promise<ChatResponse> {
  return apiRequest<ChatResponse>("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listSessions(): Promise<SessionSummary[]> {
  return apiRequest<SessionSummary[]>("/sessions");
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  return apiRequest<SessionDetail>(`/sessions/${encodeURIComponent(sessionId)}`);
}

export async function deleteSession(sessionId: string): Promise<{ deleted: boolean; session_id: string }> {
  return apiRequest<{ deleted: boolean; session_id: string }>(`/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

export async function listGapTickets(status = "open"): Promise<GapTicket[]> {
  return apiRequest<GapTicket[]>(`/gaps?status=${encodeURIComponent(status)}`);
}

export async function getGapTicket(id: string): Promise<GapTicket> {
  return apiRequest<GapTicket>(`/gaps/${encodeURIComponent(id)}`);
}

export async function assignGapTicket(id: string, assignee_user_id: string): Promise<GapTicket> {
  return apiRequest<GapTicket>(`/gaps/${encodeURIComponent(id)}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assignee_user_id }),
  });
}

export async function resolveGapTicket(
  id: string,
  payload: {
    action: "add_document" | "deprecate" | "update_document";
    document_path?: string;
    source_id?: string;
    notes?: string;
  },
): Promise<GapTicket> {
  return apiRequest<GapTicket>(`/gaps/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteGapTicket(id: string): Promise<{ deleted: boolean; id: string }> {
  return apiRequest<{ deleted: boolean; id: string }>(`/gaps/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function getAuditBySession(sessionId: string): Promise<AuditEntry[]> {
  return apiRequest<AuditEntry[]>(`/audit/session/${encodeURIComponent(sessionId)}`);
}

export async function getAuditByQuery(queryId: string): Promise<AuditEntry> {
  return apiRequest<AuditEntry>(`/audit/query/${encodeURIComponent(queryId)}`);
}

export async function exportAuditCsv(): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/audit/export`, {
    headers: {
      ...authHeader(),
    },
  });
  if (!response.ok) {
    throw new ApiError(await parseErrorMessage(response), response.status);
  }
  return response.blob();
}

export async function triggerIndexing(formData: FormData): Promise<unknown> {
  return apiRequest("/indexing/index", {
    method: "POST",
    body: formData,
  });
}

export async function triggerReindex(): Promise<unknown> {
  return apiRequest("/indexing/reindex", {
    method: "POST",
  });
}

export async function listSources(): Promise<unknown> {
  return apiRequest("/indexing/sources");
}

export async function updateTrustScore(sourceId: string, trust_score: number): Promise<unknown> {
  return apiRequest(`/indexing/trust/${encodeURIComponent(sourceId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trust_score }),
  });
}

export { ApiError };
