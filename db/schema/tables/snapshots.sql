CREATE TABLE snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    ref TEXT NOT NULL,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, ref, path)
);
