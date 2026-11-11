ALTER TABLE sessions ADD COLUMN processing_token uuid;
ALTER TABLE sessions ADD COLUMN access_token_hash text;
ALTER TABLE sessions ADD COLUMN processing_started_at timestamptz;
ALTER TABLE messages ADD COLUMN request_id uuid;
ALTER TABLE messages ADD COLUMN status text NOT NULL DEFAULT 'completed'
    CHECK (status IN ('processing', 'completed', 'failed'));
CREATE UNIQUE INDEX messages_request_role_idx ON messages(session_id, request_id, role)
    WHERE request_id IS NOT NULL;
-- Preserve any historic duplicates, but enforce one current lead for new code via session lock.
