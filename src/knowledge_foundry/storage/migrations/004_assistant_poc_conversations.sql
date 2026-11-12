-- Additive POC storage. Legacy sessions, messages and provenance are untouched.
CREATE TABLE kf_assistant_conversations (
    id uuid PRIMARY KEY,
    owner_hash text NOT NULL,
    document_snapshot_id text NOT NULL REFERENCES kf_snapshots(id),
    accepted_snapshot_id text NOT NULL REFERENCES kf_snapshots(id),
    binding jsonb NOT NULL,
    binding_sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kf_assistant_conversations_owner ON kf_assistant_conversations(owner_hash, created_at);
CREATE TABLE kf_assistant_messages (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES kf_assistant_conversations(id),
    submission_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('user','assistant')),
    content text NOT NULL,
    payload jsonb NOT NULL,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, submission_id, role)
);
CREATE INDEX kf_assistant_messages_conversation ON kf_assistant_messages(conversation_id, sequence);
