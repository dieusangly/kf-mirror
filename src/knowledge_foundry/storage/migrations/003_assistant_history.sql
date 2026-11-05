-- Existing conversations remain document-assistant conversations.
ALTER TABLE kf_sessions ADD COLUMN assistant_mode text NOT NULL DEFAULT 'document'
    CHECK (assistant_mode IN ('document', 'reasoning'));
CREATE INDEX kf_sessions_mode ON kf_sessions(owner_hash, snapshot_id, assistant_mode, created_at);
