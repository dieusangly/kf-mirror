CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE kf_snapshots (
    id text PRIMARY KEY,
    corpus text NOT NULL,
    identity jsonb NOT NULL,
    embedding_profile jsonb,
    status text NOT NULL CHECK (status IN ('ready', 'published')),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    UNIQUE (corpus, id)
);
CREATE TABLE kf_corpora (
    corpus text PRIMARY KEY,
    active_snapshot text,
    FOREIGN KEY (corpus, active_snapshot) REFERENCES kf_snapshots(corpus, id)
);
CREATE TABLE kf_vectors (
    id text PRIMARY KEY,
    identity jsonb NOT NULL,
    embedding vector NOT NULL
);
CREATE TABLE kf_records (
    snapshot_id text NOT NULL REFERENCES kf_snapshots(id),
    id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('passage','claim','summary')),
    text text NOT NULL,
    source_ids text[] NOT NULL,
    document_ids text[] NOT NULL,
    evidence_ids text[] NOT NULL,
    metadata jsonb NOT NULL,
    vector_id text REFERENCES kf_vectors(id),
    terms tsvector NOT NULL,
    PRIMARY KEY (snapshot_id, id)
);
CREATE INDEX kf_records_scope ON kf_records(snapshot_id, kind);
CREATE INDEX kf_records_terms ON kf_records USING gin(terms);
CREATE INDEX kf_records_sources ON kf_records USING gin(source_ids);
CREATE INDEX kf_records_documents ON kf_records USING gin(document_ids);
CREATE INDEX kf_records_evidence ON kf_records USING gin(evidence_ids);
CREATE INDEX kf_records_vector ON kf_records(vector_id);
CREATE TABLE kf_evidence (
    snapshot_id text NOT NULL REFERENCES kf_snapshots(id),
    id text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY(snapshot_id, id)
);
CREATE TABLE kf_sessions (
    id uuid PRIMARY KEY,
    owner_hash text NOT NULL,
    snapshot_id text NOT NULL REFERENCES kf_snapshots(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kf_sessions_owner ON kf_sessions(owner_hash, snapshot_id, created_at);
CREATE INDEX kf_sessions_snapshot ON kf_sessions(snapshot_id);
CREATE TABLE kf_messages (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES kf_sessions(id),
    role text NOT NULL CHECK (role IN ('user','assistant')),
    content text NOT NULL,
    payload jsonb NOT NULL,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kf_messages_session ON kf_messages(session_id, sequence);
CREATE TABLE kf_feedback (
    message_id uuid PRIMARY KEY REFERENCES kf_messages(id),
    rating integer NOT NULL CHECK (rating IN (-1,1)),
    comment text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);
