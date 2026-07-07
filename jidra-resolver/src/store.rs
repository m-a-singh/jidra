use rusqlite::{Connection, Result, params};
use crate::models::{ResolvedCallSite, ResolvedCallEdge};

/// Open a SQLite connection with WAL mode and NORMAL synchronous.
pub fn open(db_path: &str) -> Result<Connection> {
    let conn = Connection::open(db_path)?;
    conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;
    Ok(conn)
}

/// Classify a file path as "main" or "test" — mirrors Python's infer_variant_split.
pub fn infer_variant(file_path: &str) -> &'static str {
    let normalized = file_path.replace('\\', "/");
    let filename = normalized.rsplit('/').next().unwrap_or("");
    if normalized.contains("/src/test/")
        || normalized.contains("/tests/")
        || normalized.contains("/test/")
        || (filename.starts_with("test_") && filename.ends_with(".py"))
        || (filename.ends_with("_test.py"))
    {
        "test"
    } else {
        "main"
    }
}

/// Write resolution results for a batch of callsites.
/// Wraps everything in a single transaction:
/// - Deletes existing resolved_call_edges for both variants in this module_id scope
/// - Inserts new resolved_call_edges with per-edge variant derived from callsite file_path
/// - Updates callsite resolution columns for resolved callsites
pub fn write_resolution(
    conn: &Connection,
    resolved: &[ResolvedCallSite],
    edges: &[ResolvedCallEdge],
    // variant is now unused (derived per-edge); kept for API compat
    _variant: &str,
    module_id: Option<&str>,
    // callsite_variant: (callsite_id -> variant) precomputed
    callsite_variant: &std::collections::HashMap<String, &'static str>,
) -> Result<()> {
    conn.execute("BEGIN", [])?;

    let result = (|| -> Result<()> {
        // Delete existing edges for both variants in this module_id scope.
        for v in &["main", "test"] {
            conn.execute(
                "DELETE FROM resolved_call_edges WHERE variant = ?1 AND module_id IS ?2",
                params![v, module_id],
            )?;
        }

        // Batch INSERT resolved_call_edges with per-edge variant derived from callsite file_path.
        {
            let mut stmt = conn.prepare(
                "INSERT INTO resolved_call_edges \
                 (id, variant, module_id, callsite_id, caller_method_id, callee_method_id) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            )?;
            for edge in edges {
                let edge_variant = callsite_variant
                    .get(&edge.callsite_id)
                    .copied()
                    .unwrap_or("main");
                stmt.execute(params![
                    edge.id,
                    edge_variant,
                    module_id,
                    edge.callsite_id,
                    edge.caller_method_id,
                    edge.callee_method_id,
                ])?;
            }
        }

        // Batch UPDATE callsites resolution columns — only for non-unresolved callsites.
        // Unresolved callsites already have resolution_status = "unresolved" from Python insert.
        {
            let mut stmt = conn.prepare(
                "UPDATE callsites SET \
                 receiver_type_normalized = ?1, \
                 receiver_resolution_source = ?2, \
                 receiver_type = ?3, \
                 resolved_candidates_json = ?4, \
                 resolution_status = ?5, \
                 resolution_reason = ?6, \
                 candidate_count = ?7 \
                 WHERE id = ?8",
            )?;
            for cs in resolved {
                if cs.resolution_status == "unresolved" {
                    continue;
                }
                let candidates_json = serde_json::to_string(&cs.resolved_candidates)
                    .unwrap_or_else(|_| "[]".to_string());
                let receiver_type: Option<&str> = cs.resolved_candidates.first().map(|s| s.as_str());
                stmt.execute(params![
                    cs.receiver_type_normalized,
                    cs.receiver_resolution_source,
                    receiver_type,
                    candidates_json,
                    cs.resolution_status,
                    cs.resolution_reason,
                    cs.candidate_count as i64,
                    cs.id,
                ])?;
            }
        }

        Ok(())
    })();

    match result {
        Ok(()) => {
            conn.execute("COMMIT", [])?;
            Ok(())
        }
        Err(e) => {
            let _ = conn.execute("ROLLBACK", []);
            Err(e)
        }
    }
}
