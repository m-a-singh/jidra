use pyo3::prelude::*;

mod models;
mod normalize;
pub mod lookup;
pub mod resolver;
mod store;

#[pyfunction]
fn resolve_and_store(
    methods: Vec<models::MethodData>,
    classes: Vec<models::ClassData>,
    callsites: Vec<models::CallSiteData>,
    inheritance_edges: Vec<models::EdgeData>,
    fields: Vec<models::FieldData>,
    db_path: &str,
    #[allow(unused_variables)] variant: &str,
    module_id: Option<&str>,
) -> PyResult<models::ResolveStats> {
    use std::collections::HashMap;
    let start = std::time::Instant::now();

    // Build per-callsite variant map from file_path — avoids needing two separate Rust calls.
    let callsite_variant: HashMap<String, &'static str> = callsites
        .iter()
        .map(|cs| (cs.id.clone(), store::infer_variant(&cs.file_path)))
        .collect();

    // Build lookup tables
    let lookup = lookup::LookupTables::build(&methods, &classes, &fields, &inheritance_edges, &callsites);

    // Run 8-pass resolution
    let (resolved, edges) = resolver::resolve_calls(&callsites, &lookup);

    // Write resolution output to SQLite
    let conn = store::open(db_path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    store::write_resolution(&conn, &resolved, &edges, variant, module_id, &callsite_variant)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    let total = callsites.len();
    let resolved_count = resolved
        .iter()
        .filter(|r| r.resolution_status.starts_with("resolved"))
        .count();
    let external = resolved
        .iter()
        .filter(|r| r.resolution_status == "external_library")
        .count();

    Ok(models::ResolveStats {
        total_callsites: total,
        resolved: resolved_count,
        unresolved: total - resolved_count - external,
        external_library: external,
        duration_ms: start.elapsed().as_millis() as u64,
    })
}

#[pyfunction]
fn resolve_incremental(
    _new_methods: Vec<models::MethodData>,
    _new_classes: Vec<models::ClassData>,
    _new_callsites: Vec<models::CallSiteData>,
    _db_path: &str,
    _variant: &str,
    _deleted_files: Vec<String>,
    _only_caller_ids: Option<Vec<String>>,
) -> PyResult<()> {
    Err(pyo3::exceptions::PyNotImplementedError::new_err("not yet implemented"))
}

#[pyfunction]
fn patch_line_numbers(
    _patches: Vec<models::LinePatch>,
    _db_path: &str,
    _variant: &str,
) -> PyResult<()> {
    Err(pyo3::exceptions::PyNotImplementedError::new_err("not yet implemented"))
}

#[pymodule]
fn jidra_resolver(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<models::ResolveStats>()?;
    m.add_function(wrap_pyfunction!(resolve_and_store, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_incremental, m)?)?;
    m.add_function(wrap_pyfunction!(patch_line_numbers, m)?)?;
    Ok(())
}
