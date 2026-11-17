CREATE TABLE changes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    watcher TEXT NOT NULL,
    url TEXT NOT NULL,
    verb TEXT NOT NULL,
    old_content TEXT,
    new_content TEXT,
    diff TEXT
);
