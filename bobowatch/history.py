"""Complete SQL results for retained-history dashboard ranges.

Raw observations stay in SQL. Large queries return per-replica/time-bucket
counter sums, compatible histograms and the actual latest sample for gauges.
Every endpoint and observation contributes; grouping is presentation only.
"""

from .protocol import BUCKETS_US


def aggregate_history(conn, replicas, since, until, step, *, exclusive_end=False):
    if not replicas:
        return [], {}
    params = {"since": since, "until": until, "step": step}
    for i, replica in enumerate(replicas):
        params[f"replica{i}"] = replica
    marks = ",".join(f":replica{i}" for i in range(len(replicas)))
    end = "<" if exclusive_end else "<="
    selected = f"""SELECT n.*,u.replica_id AS replica,r.site_id AS site,
        u.started_at_ms AS run_started_at_ms,
        CAST(n.observed_at_ms / :step AS BIGINT) AS time_bucket,
        CASE WHEN h.snapshot_id IS NULL THEN 0 ELSE 1 END AS extended,
        h.in_flight,h.delta_bytes_sent,h.delta_bytes_received,h.delta_sized_requests
        FROM snapshots n JOIN runs u ON u.id=n.run_id
        JOIN replicas r ON r.id=u.replica_id
        LEFT JOIN http_samples h ON h.snapshot_id=n.id
        WHERE u.replica_id IN ({marks}) AND n.observed_at_ms>=:since
        AND n.observed_at_ms {end} :until"""
    # The ranked row is also retained separately: a bucket mean must never
    # replace the latest single-interval current rate or sampled gauge.
    grouped = conn.execute(
        f"""WITH selected AS ({selected}), ranked AS (
            SELECT selected.*,ROW_NUMBER() OVER (
                PARTITION BY replica,time_bucket,extended
                ORDER BY observed_at_ms DESC,received_at_ms DESC,
                         run_started_at_ms DESC,sequence DESC,id DESC) AS newest
            FROM selected)
        SELECT replica,site,time_bucket,extended,COUNT(*) AS snapshot_count,
            MAX(observed_at_ms) AS observed_at_ms,
            SUM(delta_requests) AS delta_requests,SUM(delta_errors) AS delta_errors,
            SUM(delta_duration_us) AS delta_duration_us,SUM(interval_ms) AS interval_ms,
            MAX(partial) AS partial,MAX(skewed) AS skewed,
            SUM(partial) AS partial_snapshot_count,SUM(skewed) AS skewed_snapshot_count,
            SUM(delta_bytes_sent) AS delta_bytes_sent,
            SUM(delta_bytes_received) AS delta_bytes_received,
            SUM(delta_sized_requests) AS delta_sized_requests,
            MAX(CASE WHEN newest=1 THEN received_at_ms END) AS received_at_ms,
            MAX(CASE WHEN newest=1 THEN in_flight END) AS in_flight,
            MAX(CASE WHEN newest=1 THEN delta_requests END) AS latest_requests,
            MAX(CASE WHEN newest=1 THEN interval_ms END) AS latest_interval,
            MAX(CASE WHEN newest=1 THEN partial END) AS latest_partial,
            MAX(CASE WHEN newest=1 THEN skewed END) AS latest_skewed,
            MAX(CASE WHEN newest=1 THEN run_started_at_ms END) AS run_started_at_ms,
            MAX(CASE WHEN newest=1 THEN sequence END) AS sequence
        FROM ranked GROUP BY replica,site,time_bucket,extended""",
        params,
    )
    rows, by_key = [], {}
    for record in grouped:
        data = dict(record)
        http = None
        if data["extended"]:
            http = {
                key: data[key]
                for key in (
                    "delta_bytes_sent",
                    "delta_bytes_received",
                    "delta_sized_requests",
                    "in_flight",
                )
            }
            http["codes"] = [0] * 5
        row = {
            **data,
            "http": http,
            "buckets": [0] * (len(BUCKETS_US) + 1),
            "routes": [],
            "latest": {
                "observed_at_ms": data["observed_at_ms"],
                "received_at_ms": data["received_at_ms"],
                "partial": data["latest_partial"],
                "skewed": data["latest_skewed"],
                "delta_requests": data["latest_requests"],
                "interval_ms": data["latest_interval"],
                "run_started_at_ms": data["run_started_at_ms"],
                "sequence": data["sequence"],
                "http": {"in_flight": data["in_flight"]} if http is not None else None,
            },
        }
        rows.append(row)
        by_key[data["replica"], data["time_bucket"], data["extended"]] = row
    # Separate grouped joins avoid multiplying histograms, codes and routes.
    for record in conn.execute(
        f"""WITH selected AS ({selected})
        SELECT replica,time_bucket,extended,b.bucket_index,SUM(b.delta) AS count
        FROM selected JOIN histogram_buckets b ON b.snapshot_id=selected.id
        WHERE b.delta>0 GROUP BY replica,time_bucket,extended,b.bucket_index""",
        params,
    ):
        by_key[record["replica"], record["time_bucket"], record["extended"]]["buckets"][
            record["bucket_index"]
        ] = record["count"]
    for record in conn.execute(
        f"""WITH selected AS ({selected})
        SELECT replica,time_bucket,extended,c.status_class,SUM(c.delta) AS count
        FROM selected JOIN response_classes c ON c.snapshot_id=selected.id
        WHERE c.delta>0 GROUP BY replica,time_bucket,extended,c.status_class""",
        params,
    ):
        by_key[record["replica"], record["time_bucket"], record["extended"]]["http"]["codes"][
            record["status_class"] - 1
        ] = record["count"]
    endpoints = _endpoints(conn, selected, params)
    return rows, endpoints


def _endpoints(conn, selected, params):
    # Return every template independently for combined/site/replica scopes.
    totals = conn.execute(
        f"""WITH selected AS ({selected}), per_replica AS (
            SELECT replica,site,t.route,SUM(t.delta_requests) AS requests,
                   SUM(t.delta_errors) AS errors,SUM(t.delta_duration_us) AS duration_us
            FROM selected JOIN route_totals t ON t.snapshot_id=selected.id
            GROUP BY replica,site,t.route),
        scopes AS (
            SELECT 'replica' AS kind,replica AS scope,site,route,requests,errors,duration_us FROM per_replica
            UNION ALL
            SELECT 'site',site,site,route,SUM(requests),SUM(errors),SUM(duration_us)
                FROM per_replica GROUP BY site,route
            UNION ALL
            SELECT 'all','',site,route,SUM(requests),SUM(errors),SUM(duration_us)
                FROM per_replica GROUP BY site,route)
        SELECT kind,scope,site,route,requests,errors,duration_us FROM scopes
        ORDER BY kind,scope,requests DESC,site,route""",
        params,
    )
    result = {}
    for row in totals:
        metrics = result.setdefault((row["kind"], row["scope"]), {"routes": [], "route_count": 0})
        metrics["routes"].append(
            {key: row[key] for key in ("site", "route", "requests", "errors", "duration_us")}
        )
        metrics["route_count"] += 1
    return result
