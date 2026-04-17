export type UserRole = "clinician" | "manager" | "admin" | string;

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type?: string;
}

export interface CurrentUser {
  id: string;
  email: string;
  full_name?: string | null;
  role: UserRole;
  department: string;
  is_active?: boolean;
  created_at?: string | null;
}

export interface UserSummary {
  id: string;
  email: string;
  full_name?: string | null;
  role: UserRole;
  department?: string | null;
}

export interface IndexCollectionOption {
  department: string;
  collection: string;
  label: string;
}

export interface SampleDocumentEntry {
  name: string;
  size: number;
  modified_at: number;
}

export interface SessionMessage {
  role: "user" | "assistant";
  content: string;
  citations?: string[];
  citation_details?: Array<{
    source: string;
    title: string;
    section: string;
    trust_score: number;
    excerpt?: string;
  }>;
  confidence?: number | null;
  stakes_level?: string | null;
  gap_ticket_id?: string | null;
  requires_human_review?: boolean;
  timestamp?: string;
}

export interface SessionSummary {
  session_id: string;
  user_id: string;
  created_at?: string | null;
  last_active?: string | null;
  message_count: number;
  title?: string;
}

export interface SessionDetail extends SessionSummary {
  messages: SessionMessage[];
}

export interface ChatResponse {
  answer: string;
  citations?: string[];
  citation_details?: Array<{
    source: string;
    title: string;
    section: string;
    trust_score: number;
    excerpt?: string;
  }>;
  confidence?: number | null;
  stakes_level?: "low" | "high" | string;
  gap_ticket_id?: string | null;
  gap_ticket_created?: boolean;
  requires_human_review?: boolean;
  query_id?: string;
}

export interface GapTicket {
  id: string;
  query: string;
  description: string;
  gap_type: "missing_knowledge" | "contradiction" | "low_confidence" | string;
  status: "open" | "in_progress" | "resolved" | "wont_fix" | string;
  created_by_user_id?: string | null;
  department?: string | null;
  suggested_owner?: string | null;
  conflicting_sources?: string[] | null;
  assigned_to_user_id?: string | null;
  resolution_notes?: string | null;
  resolved_by_user_id?: string | null;
  resolved_at?: string | null;
  created_at?: string | null;
}

export interface AuditEntry {
  session_id: string;
  query_id: string;
  timestamp?: string;
  user_id?: string;
  user_role?: string;
  stakes_classification?: {
    stakes_level?: string;
    query_complexity?: string;
    role_sensitivity?: string;
    consequence_severity?: string;
  };
  retrieval_path?: Record<string, unknown>;
  evidence_weighed?: string[];
  contradictions_found?: unknown[];
  alternatives_considered?: string[];
  confidence?: number;
  confidence_gate_passed?: boolean;
  requires_human_review?: boolean;
  final_answer?: string;
  citations?: string[];
}
