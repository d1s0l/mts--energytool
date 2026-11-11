ALTER TABLE sessions ADD COLUMN dialog_memory jsonb NOT NULL
    DEFAULT '{"schema_version":1,"revision":0,"facts":[],"asked":[]}'::jsonb
    CHECK (jsonb_typeof(dialog_memory) = 'object');
ALTER TABLE sessions ADD COLUMN dialog_revision bigint NOT NULL DEFAULT 0;
