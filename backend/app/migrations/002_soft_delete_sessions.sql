ALTER TABLE sessions ADD COLUMN deleted_at timestamptz;

CREATE INDEX sessions_active_created_at_idx
    ON sessions (created_at DESC, id) WHERE deleted_at IS NULL;
