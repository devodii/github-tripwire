CREATE TABLE IF NOT EXISTS changes (
    id bigint GENERATED ALWAYS AS IDENTITY,
    detected_at timestamptz DEFAULT now() NOT NULL,
    watcher text NOT NULL,
    url text NOT NULL,
    verb text NOT NULL,
    old_content text,
    new_content text,
    diff text,
    CONSTRAINT changes_pkey PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS changes_watcher_detected_at ON changes (watcher, detected_at DESC);

CREATE TABLE IF NOT EXISTS snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY,
    source text NOT NULL,
    ref text NOT NULL,
    path text NOT NULL,
    content text NOT NULL,
    fetched_at timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT snapshots_pkey PRIMARY KEY (id),
    CONSTRAINT snapshots_source_ref_path_key UNIQUE (source, ref, path)
);
