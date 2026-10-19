CREATE TABLE http_samples (
    snapshot_id VARCHAR(128) PRIMARY KEY NOT NULL REFERENCES snapshots(id),
    in_flight BIGINT,
    bytes_sent BIGINT NOT NULL,
    bytes_received BIGINT NOT NULL,
    sized_requests BIGINT NOT NULL,
    delta_bytes_sent BIGINT NOT NULL,
    delta_bytes_received BIGINT NOT NULL,
    delta_sized_requests BIGINT NOT NULL
);
CREATE TABLE response_classes (
    snapshot_id VARCHAR(128) NOT NULL REFERENCES snapshots(id),
    status_class BIGINT NOT NULL CHECK (status_class BETWEEN 1 AND 5),
    total BIGINT NOT NULL,
    delta BIGINT NOT NULL,
    PRIMARY KEY (snapshot_id, status_class)
);
CREATE TABLE route_totals (
    snapshot_id VARCHAR(128) NOT NULL REFERENCES snapshots(id),
    route VARCHAR(128) NOT NULL,
    requests BIGINT NOT NULL,
    errors BIGINT NOT NULL,
    duration_sum_us BIGINT NOT NULL,
    delta_requests BIGINT NOT NULL,
    delta_errors BIGINT NOT NULL,
    delta_duration_us BIGINT NOT NULL,
    PRIMARY KEY (snapshot_id, route)
);
