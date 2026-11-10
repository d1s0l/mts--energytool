-- История сохраняется независимо от наличия заявки.
CREATE TABLE sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    state text NOT NULL DEFAULT 'chatting'
        CHECK (state IN ('chatting', 'awaiting_contacts', 'lead_created')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX sessions_created_at_idx ON sessions (created_at DESC, id);

CREATE TABLE messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES sessions(id),
    -- Порядок сообщений не зависит от совпадения временных меток.
    position bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    text text NOT NULL CHECK (length(btrim(text)) > 0),
    debug jsonb CHECK (debug IS NULL OR jsonb_typeof(debug) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX messages_session_position_idx ON messages (session_id, position);

CREATE TABLE leads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES sessions(id),
    full_name text NOT NULL CHECK (length(btrim(full_name)) > 0),
    phone text NOT NULL CHECK (length(btrim(phone)) > 0),
    company text NOT NULL CHECK (length(btrim(company)) > 0),
    summary text NOT NULL CHECK (length(btrim(summary)) > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Число заявок на сессию пока не ограничиваем: это правило ещё обсуждается.
CREATE INDEX leads_session_id_idx ON leads (session_id);
