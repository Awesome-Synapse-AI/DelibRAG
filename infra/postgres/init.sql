CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name VARCHAR(255),
    role VARCHAR(50) NOT NULL,
    department VARCHAR(100) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    refresh_token_hash VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gap_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query TEXT NOT NULL,
    created_by_user_id UUID REFERENCES users(id),
    department VARCHAR(100),
    gap_type VARCHAR(50) NOT NULL DEFAULT 'missing_knowledge',
    description TEXT NOT NULL,
    conflicting_sources TEXT[],
    suggested_owner VARCHAR(255),
    assigned_to_user_id UUID REFERENCES users(id),
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    resolution_notes TEXT,
    resolved_by_user_id UUID REFERENCES users(id),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source_trust_scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id VARCHAR(255) UNIQUE NOT NULL,
    source_name VARCHAR(255),
    department VARCHAR(100),
    trust_score FLOAT DEFAULT 1.0,
    is_deprecated BOOLEAN DEFAULT FALSE,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS routing_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    department VARCHAR(100) NOT NULL,
    preferred_sources TEXT[],
    avoided_sources TEXT[],
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

ALTER TABLE gap_tickets ADD COLUMN IF NOT EXISTS created_by_user_id UUID REFERENCES users(id);
ALTER TABLE gap_tickets ADD COLUMN IF NOT EXISTS department VARCHAR(100);
ALTER TABLE gap_tickets ADD COLUMN IF NOT EXISTS gap_type VARCHAR(50) NOT NULL DEFAULT 'missing_knowledge';
ALTER TABLE gap_tickets ADD COLUMN IF NOT EXISTS conflicting_sources TEXT[];
ALTER TABLE gap_tickets ADD COLUMN IF NOT EXISTS suggested_owner VARCHAR(255);
ALTER TABLE gap_tickets ADD COLUMN IF NOT EXISTS assigned_to_user_id UUID REFERENCES users(id);
ALTER TABLE gap_tickets ADD COLUMN IF NOT EXISTS resolution_notes TEXT;
ALTER TABLE gap_tickets ADD COLUMN IF NOT EXISTS resolved_by_user_id UUID REFERENCES users(id);
ALTER TABLE gap_tickets ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

ALTER TABLE source_trust_scores ADD COLUMN IF NOT EXISTS source_name VARCHAR(255);
ALTER TABLE source_trust_scores ADD COLUMN IF NOT EXISTS department VARCHAR(100);
ALTER TABLE source_trust_scores ADD COLUMN IF NOT EXISTS trust_score FLOAT DEFAULT 1.0;
ALTER TABLE source_trust_scores ADD COLUMN IF NOT EXISTS is_deprecated BOOLEAN DEFAULT FALSE;
ALTER TABLE source_trust_scores ADD COLUMN IF NOT EXISTS last_updated TIMESTAMPTZ DEFAULT NOW();

ALTER TABLE routing_preferences ADD COLUMN IF NOT EXISTS preferred_sources TEXT[];
ALTER TABLE routing_preferences ADD COLUMN IF NOT EXISTS avoided_sources TEXT[];

CREATE UNIQUE INDEX IF NOT EXISTS uq_source_trust_scores_source_id ON source_trust_scores (source_id);
CREATE INDEX IF NOT EXISTS ix_gap_tickets_status_created ON gap_tickets (status, created_at DESC);
