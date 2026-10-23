CREATE TABLE storage_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    epoch VARCHAR(128) NOT NULL,
    evicted_snapshots BIGINT NOT NULL DEFAULT 0,
    evicted_events BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX snapshots_receipt ON snapshots(received_at_ms, id);
ALTER TABLE events ADD COLUMN received_at_ms BIGINT NOT NULL DEFAULT 0;
CREATE INDEX events_receipt ON events(received_at_ms, id);
CREATE TABLE event_receipts (
    id VARCHAR(128) PRIMARY KEY NOT NULL,
    replica_id VARCHAR(128) NOT NULL REFERENCES replicas(id),
    run_id VARCHAR(128) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    received_at_ms BIGINT NOT NULL
);
CREATE INDEX event_receipts_replica ON event_receipts(replica_id, received_at_ms);
