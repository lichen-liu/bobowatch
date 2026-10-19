CREATE TABLE schema_migrations (
    version BIGINT PRIMARY KEY NOT NULL,
    checksum VARCHAR(64) NOT NULL
);
CREATE TABLE projects (id VARCHAR(128) PRIMARY KEY NOT NULL, title VARCHAR(256) NOT NULL);
CREATE TABLE sites (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    project_id VARCHAR(128) NOT NULL REFERENCES projects(id),
    title VARCHAR(256) NOT NULL,
    environment VARCHAR(128) NOT NULL
);
CREATE TABLE hosts (id VARCHAR(128) PRIMARY KEY NOT NULL, title VARCHAR(256) NOT NULL);
CREATE TABLE replicas (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    site_id VARCHAR(128) NOT NULL REFERENCES sites(id),
    host_id VARCHAR(128) NOT NULL REFERENCES hosts(id),
    title VARCHAR(256) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    retired INTEGER NOT NULL CHECK (retired IN (0, 1))
);
CREATE TABLE runs (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    replica_id VARCHAR(128) NOT NULL REFERENCES replicas(id),
    started_at_ms BIGINT NOT NULL,
    revision VARCHAR(128) NOT NULL
);
CREATE INDEX runs_replica ON runs(replica_id, started_at_ms);
CREATE TABLE snapshots (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    run_id VARCHAR(128) NOT NULL REFERENCES runs(id),
    sequence BIGINT NOT NULL,
    observed_at_ms BIGINT NOT NULL,
    received_at_ms BIGINT NOT NULL,
    elapsed_ms BIGINT NOT NULL,
    requests BIGINT NOT NULL,
    errors BIGINT NOT NULL,
    duration_sum_us BIGINT NOT NULL,
    delta_requests BIGINT NOT NULL,
    delta_errors BIGINT NOT NULL,
    delta_duration_us BIGINT NOT NULL,
    interval_ms BIGINT NOT NULL,
    partial INTEGER NOT NULL CHECK (partial IN (0, 1)),
    skewed INTEGER NOT NULL CHECK (skewed IN (0, 1)),
    payload_hash VARCHAR(64) NOT NULL,
    UNIQUE (run_id, sequence)
);
CREATE INDEX snapshots_run ON snapshots(run_id, sequence);
CREATE INDEX snapshots_time ON snapshots(observed_at_ms);
CREATE TABLE histogram_buckets (
    snapshot_id VARCHAR(128) NOT NULL REFERENCES snapshots(id),
    bucket_index BIGINT NOT NULL,
    total BIGINT NOT NULL,
    delta BIGINT NOT NULL,
    PRIMARY KEY (snapshot_id, bucket_index)
);
CREATE TABLE events (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    run_id VARCHAR(128) NOT NULL REFERENCES runs(id),
    occurred_at_ms BIGINT NOT NULL,
    severity VARCHAR(16) NOT NULL,
    message VARCHAR(1024) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL
);
CREATE INDEX events_time ON events(occurred_at_ms);
