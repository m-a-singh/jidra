use std::collections::{HashMap, HashSet, VecDeque};

use once_cell::sync::Lazy;
use regex::Regex;

use crate::lookup::LookupTables;
use crate::models::{CallSiteData, ClassData, MethodData, ResolvedCallEdge, ResolvedCallSite};
use crate::normalize::{normalize_type, strip_generic};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_CHAIN_PASSES: usize = 6;
const CHA_MAX_SUBTYPES: usize = 150;

static KNOWN_GLOBALS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "console", "Math", "JSON", "Promise", "Array", "Object", "String", "Number",
        "Boolean", "Symbol", "RegExp", "Map", "Set", "Date", "Error", "parseInt",
        "parseFloat", "isNaN", "isFinite", "encodeURIComponent", "decodeURIComponent",
        "fetch", "process", "Buffer", "setTimeout", "setInterval", "clearTimeout",
        "clearInterval",
        // React hooks (bare calls)
        "useState", "useEffect", "useCallback", "useMemo", "useRef", "useContext",
        "useReducer", "useLayoutEffect", "useImperativeHandle", "useDebugValue",
        "useId", "useDeferredValue", "useTransition",
        // i18n
        "t", "i18n",
    ]
    .iter()
    .copied()
    .collect()
});

static REACT_HOOK_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^use[A-Z]").unwrap());

static TS_EXTENSIONS: &[&str] = &[".ts", ".tsx", ".js", ".jsx", ".mjs"];

static CHA_SKIP_METHODS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "name", "toString", "equals", "hashCode", "getClass", "compareTo",
        "ordinal", "getValue", "getId", "getType", "getCode", "getKey",
        "clone", "notify", "notifyAll", "wait", "finalize",
    ]
    .iter()
    .copied()
    .collect()
});

static ALREADY_RESOLVED: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "resolved",
        "resolved_inherited",
        "resolved_via_import",
        "resolved_same_package",
        "resolved_same_class",
        "resolved_via_sole_implementation",
        "candidate_global_name_arity",
        "ambiguous_overload",
        "resolved_impl_suffix",
    ]
    .iter()
    .copied()
    .collect()
});

// ---------------------------------------------------------------------------
// Per-callsite mutable resolution state (parallel to callsites vec)
// ---------------------------------------------------------------------------

struct ResolvedState {
    resolution_status: String,
    resolution_reason: String,
    resolved_candidates: Vec<String>,
    candidate_count: usize,
    receiver_type_raw: Option<String>,
    receiver_type_normalized: Option<String>,
    receiver_resolution_source: Option<String>,
}

impl ResolvedState {
    fn new(callsite: &CallSiteData) -> Self {
        Self {
            resolution_status: "unresolved".to_string(),
            resolution_reason: String::new(),
            resolved_candidates: vec![],
            candidate_count: 0,
            receiver_type_raw: callsite.receiver_type_raw.clone(),
            receiver_type_normalized: None,
            receiver_resolution_source: None,
        }
    }
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

pub fn resolve_calls(
    callsites: &[CallSiteData],
    lookup: &LookupTables,
) -> (Vec<ResolvedCallSite>, Vec<ResolvedCallEdge>) {
    let t0 = std::time::Instant::now();
    let mut states: Vec<ResolvedState> = callsites.iter().map(ResolvedState::new).collect();

    // Pass 0 — compiler_symbol validation.
    // CallSiteData has no resolution_status on input; the Python extractor handles
    // TS compiler symbols before handing data to Rust. Nothing to do here.

    // Pass 1 — main resolution cascade, one callsite at a time.
    // norm_cache memoizes normalize_type results keyed by (receiver_type_raw, caller_class_fqn)
    // so classes with many callsites sharing the same receiver type only pay the import-scan cost once.
    // norm_cache memoizes normalize_type results keyed by (receiver_type_raw, caller_class_fqn)
    // so classes with many callsites sharing the same receiver type only pay the import-scan cost once.
    let mut norm_cache: HashMap<(String, String), (Option<String>, Option<String>, Vec<String>)> = HashMap::new();
    // Memoize BFS hierarchy walks: same (class, method) always yields same result.
    let mut hier_cache: HashMap<(String, String), (Vec<MethodData>, Option<String>)> = HashMap::new();
    for idx in 0..callsites.len() {
        let mut seen = HashSet::new();
        resolve_one(idx, callsites, &mut states, lookup, &mut seen, &mut norm_cache, &mut hier_cache);
    }
    eprintln!("[pass-timing] pass1={:.3}s", t0.elapsed().as_secs_f64());

    // Pass 2 — chain passes: retry unresolved_receiver callsites whose receiver
    // text is the full text of an inner callsite now resolved (fluent chains).
    for _ in 0..MAX_CHAIN_PASSES {
        let mut changed = false;
        for idx in 0..callsites.len() {
            if states[idx].resolution_status != "unresolved_receiver" {
                continue;
            }
            let receiver = match callsites[idx].receiver.as_deref() {
                Some(r) => r.to_string(),
                None => continue,
            };
            if !receiver.trim_end().ends_with(')') {
                continue;
            }
            let key = (callsites[idx].caller_method_id.clone(), receiver.clone());
            let inner_idx = match lookup.chain_index.get(&key) {
                Some(&i) => i,
                None => continue,
            };
            if states[inner_idx].resolved_candidates.is_empty() {
                continue;
            }
            let inner_method_id = states[inner_idx].resolved_candidates[0].clone();
            let inner_method = match lookup.method_by_id.get(&inner_method_id) {
                Some(m) => m,
                None => continue,
            };
            if inner_method.return_type.is_empty() {
                continue;
            }
            let new_raw = strip_generic(&inner_method.return_type);
            if states[idx].receiver_type_raw.as_deref() == Some(&new_raw) {
                continue;
            }
            states[idx].receiver_type_raw = Some(new_raw);
            states[idx].receiver_resolution_source = None;
            let mut seen = HashSet::new();
            resolve_one(idx, callsites, &mut states, lookup, &mut seen, &mut norm_cache, &mut hier_cache);
            changed = true;
        }
        if !changed {
            break;
        }
    }
    eprintln!("[pass-timing] pass2={:.3}s", t0.elapsed().as_secs_f64());

    // Pass 3 — TS/JS global reclassification.
    for idx in 0..callsites.len() {
        let status = &states[idx].resolution_status;
        if status != "unresolved_receiver"
            && status != "unresolved"
            && status != "unresolved_method"
        {
            continue;
        }
        if !TS_EXTENSIONS
            .iter()
            .any(|ext| callsites[idx].file_path.ends_with(ext))
        {
            continue;
        }
        let receiver = callsites[idx].receiver.as_deref().unwrap_or("").trim();
        let callee = &callsites[idx].callee_name;
        // Only reclassify unresolved_method when there is no receiver.
        if states[idx].resolution_status == "unresolved_method" && !receiver.is_empty() {
            continue;
        }
        if KNOWN_GLOBALS.contains(receiver)
            || (receiver.is_empty() && KNOWN_GLOBALS.contains(callee.as_str()))
            || (receiver.is_empty() && REACT_HOOK_RE.is_match(callee))
        {
            states[idx].resolution_status = "external_library".to_string();
            states[idx].resolution_reason = "known global or react hook".to_string();
        }
    }
    eprintln!("[pass-timing] pass3={:.3}s", t0.elapsed().as_secs_f64());

    // Pass 4 — find_method_relaxed: second attempt for unresolved_method where
    // the receiver class is indexed but the strict BFS missed an inherited method.
    for idx in 0..callsites.len() {
        if states[idx].resolution_status != "unresolved_method" {
            continue;
        }
        let normalized = match states[idx].receiver_type_normalized.clone() {
            Some(n) => n,
            None => continue,
        };
        if !lookup.all_class_full_names.contains(&normalized) {
            continue;
        }
        let callee_name = callsites[idx].callee_name.clone();
        let argument_count = callsites[idx].argument_count;
        let (inherited, found_in) =
            find_method_relaxed(&normalized, &callee_name, argument_count, lookup);
        if inherited.is_empty() {
            continue;
        }
        let mut ids: Vec<String> = inherited.iter().map(|m| m.id.clone()).collect();
        ids.sort();
        ids.dedup();
        let count = ids.len();
        let found_label = found_in.as_deref().unwrap_or("?").to_string();
        states[idx].resolved_candidates = ids;
        states[idx].candidate_count = count;
        states[idx].resolution_status = if count == 1 {
            "resolved_inherited".to_string()
        } else {
            "ambiguous_overload".to_string()
        };
        states[idx].resolution_reason =
            format!("second-pass relaxed hierarchy from {}", found_label);
    }
    eprintln!("[pass-timing] pass4={:.3}s", t0.elapsed().as_secs_f64());

    // Pass 5 — impl-suffix heuristic: XxxService → XxxServiceImpl.
    for idx in 0..callsites.len() {
        if states[idx].resolution_status != "unresolved_receiver" {
            continue;
        }
        let receiver = match callsites[idx].receiver.as_deref() {
            Some(r) => r.to_string(),
            None => continue,
        };
        if receiver.contains('.') || receiver.trim_end().ends_with(')') {
            continue;
        }
        let caller_method = match lookup.method_by_id.get(&callsites[idx].caller_method_id) {
            Some(m) => m,
            None => continue,
        };
        let field_type = match lookup
            .fields_by_class
            .get(&caller_method.class_full_name)
            .and_then(|fields| fields.get(&receiver))
        {
            Some(t) => t.clone(),
            None => continue,
        };
        // short_type = last segment of FQN after stripping generics
        let stripped = strip_generic(&field_type);
        let short_type = stripped.split('.').last().unwrap_or(&stripped).split('<').next().unwrap_or("");
        let impl_name = format!("{}Impl", short_type);
        let impl_fqns = match lookup.class_by_short_name.get(&impl_name) {
            Some(v) => v.clone(),
            None => continue,
        };
        if impl_fqns.len() != 1 {
            continue;
        }
        let impl_fqn = impl_fqns[0].clone();
        let callee_name = callsites[idx].callee_name.clone();
        let argument_count = callsites[idx].argument_count;
        let impl_matches = lookup
            .methods_by_full_class_and_name
            .get(&(impl_fqn.clone(), callee_name.clone()))
            .cloned()
            .unwrap_or_default();
        if impl_matches.is_empty() {
            continue;
        }
        let arity_filtered: Vec<MethodData> = if argument_count >= 0 {
            impl_matches
                .iter()
                .filter(|m| m.parameter_types.len() == argument_count as usize)
                .cloned()
                .collect()
        } else {
            impl_matches.clone()
        };
        let candidates = if arity_filtered.is_empty() {
            impl_matches
        } else {
            arity_filtered
        };
        let mut ids: Vec<String> = candidates.iter().map(|m| m.id.clone()).collect();
        ids.sort();
        let count = ids.len();
        let short_type_owned = short_type.to_string();
        states[idx].receiver_type_raw = Some(impl_fqn.clone());
        states[idx].receiver_type_normalized = Some(impl_fqn.clone());
        states[idx].receiver_resolution_source = Some("impl-suffix".to_string());
        states[idx].resolved_candidates = ids;
        states[idx].candidate_count = count;
        states[idx].resolution_status = if count == 1 {
            "resolved_impl_suffix".to_string()
        } else {
            "ambiguous_overload".to_string()
        };
        states[idx].resolution_reason =
            format!("impl-suffix heuristic: {} → {}", short_type_owned, impl_fqn);
    }
    eprintln!("[pass-timing] pass5={:.3}s", t0.elapsed().as_secs_f64());

    // Pass 6 — CHA fan-out.
    let mut cha_cache: HashMap<String, Vec<String>> = HashMap::new();
    for idx in 0..callsites.len() {
        let callee_name = callsites[idx].callee_name.clone();
        if CHA_SKIP_METHODS.contains(callee_name.as_str()) {
            continue;
        }
        let argument_count = callsites[idx].argument_count;
        let status = states[idx].resolution_status.clone();

        if status == "unresolved_method" {
            // Case 1: receiver type known but method not found — fan out to subtypes.
            let recv_fqn = match states[idx].receiver_type_normalized.clone() {
                Some(n) => n,
                None => continue,
            };
            let subtypes = cha_cache.entry(recv_fqn.clone()).or_insert_with(|| cha_subtypes(&recv_fqn, lookup)).clone();
            if subtypes.is_empty() || subtypes.len() > CHA_MAX_SUBTYPES {
                continue;
            }
            let mut cha_candidates: Vec<MethodData> = vec![];
            for sub_fqn in &subtypes {
                let sub_matches = lookup
                    .methods_by_full_class_and_name
                    .get(&(sub_fqn.clone(), callee_name.clone()))
                    .cloned()
                    .unwrap_or_default();
                if argument_count >= 0 {
                    let sub_arity: Vec<MethodData> = sub_matches
                        .iter()
                        .filter(|m| m.parameter_types.len() == argument_count as usize)
                        .cloned()
                        .collect();
                    if !sub_arity.is_empty() {
                        cha_candidates.extend(sub_arity);
                    } else {
                        cha_candidates.extend(sub_matches);
                    }
                } else {
                    cha_candidates.extend(sub_matches);
                }
            }
            if cha_candidates.is_empty() {
                continue;
            }
            let cand_count = cha_candidates.len();
            let mut ids: Vec<String> = cha_candidates.iter().map(|m| m.id.clone()).collect();
            ids.sort();
            ids.dedup();
            let count = ids.len();
            let short_recv = recv_fqn.split('.').last().unwrap_or(&recv_fqn).to_string();
            states[idx].resolved_candidates = ids;
            states[idx].candidate_count = count;
            states[idx].resolution_status = "resolved_cha".to_string();
            states[idx].resolution_reason = format!(
                "CHA: {} → {} subtypes, {} candidates",
                short_recv,
                subtypes.len(),
                cand_count
            );
        } else if ALREADY_RESOLVED.contains(status.as_str())
            && !states[idx].resolved_candidates.is_empty()
        {
            // Case 2: already resolved — expand to concrete subtypes as well.
            let cand_id = states[idx].resolved_candidates[0].clone();
            let cand_method = match lookup.method_by_id.get(&cand_id) {
                Some(m) => m,
                None => continue,
            };
            let callee_class = cand_method.class_full_name.clone();
            let subtypes = cha_cache.entry(callee_class.clone()).or_insert_with(|| cha_subtypes(&callee_class, lookup)).clone();
            if subtypes.is_empty() || subtypes.len() > CHA_MAX_SUBTYPES {
                continue;
            }
            let mut extra: Vec<MethodData> = vec![];
            for sub_fqn in &subtypes {
                let sub_matches = lookup
                    .methods_by_full_class_and_name
                    .get(&(sub_fqn.clone(), callee_name.clone()))
                    .cloned()
                    .unwrap_or_default();
                if argument_count >= 0 {
                    let sub_arity: Vec<MethodData> = sub_matches
                        .iter()
                        .filter(|m| m.parameter_types.len() == argument_count as usize)
                        .cloned()
                        .collect();
                    if !sub_arity.is_empty() {
                        extra.extend(sub_arity);
                    } else {
                        extra.extend(sub_matches);
                    }
                } else {
                    extra.extend(sub_matches);
                }
            }
            if extra.is_empty() {
                continue;
            }
            let extra_count = extra.len();
            let extra_ids: Vec<String> = extra.iter().map(|m| m.id.clone()).collect();
            let mut all_ids: Vec<String> = states[idx]
                .resolved_candidates
                .iter()
                .cloned()
                .chain(extra_ids)
                .collect();
            all_ids.sort();
            all_ids.dedup();
            let count = all_ids.len();
            let short_callee_class = callee_class
                .split('.')
                .last()
                .unwrap_or(&callee_class)
                .to_string();
            states[idx].resolved_candidates = all_ids;
            states[idx].candidate_count = count;
            states[idx].resolution_status = "resolved_cha".to_string();
            states[idx].resolution_reason = format!(
                "CHA: {} → {} subtypes, {} extra",
                short_callee_class,
                subtypes.len(),
                extra_count
            );
        }
    }
    eprintln!("[pass-timing] pass6={:.3}s", t0.elapsed().as_secs_f64());

    // Pass 7 — collect output and build edges.
    let mut resolved_callsites = Vec::with_capacity(callsites.len());
    let mut edges: Vec<ResolvedCallEdge> = Vec::new();

    for (idx, callsite) in callsites.iter().enumerate() {
        let state = &states[idx];
        for callee_id in &state.resolved_candidates {
            edges.push(ResolvedCallEdge {
                id: format!("rce_{}_{}", callsite.id, callee_id),
                callsite_id: callsite.id.clone(),
                caller_method_id: callsite.caller_method_id.clone(),
                callee_method_id: callee_id.clone(),
            });
        }
        resolved_callsites.push(ResolvedCallSite {
            id: callsite.id.clone(),
            caller_method_id: callsite.caller_method_id.clone(),
            resolution_status: state.resolution_status.clone(),
            resolution_reason: state.resolution_reason.clone(),
            resolved_candidates: state.resolved_candidates.clone(),
            candidate_count: state.candidate_count,
            receiver_type_normalized: state.receiver_type_normalized.clone(),
            receiver_resolution_source: state.receiver_resolution_source.clone(),
        });
    }

    (resolved_callsites, edges)
}

// ---------------------------------------------------------------------------
// Pass 1: main resolution cascade
// ---------------------------------------------------------------------------

fn resolve_one(
    idx: usize,
    callsites: &[CallSiteData],
    states: &mut Vec<ResolvedState>,
    lookup: &LookupTables,
    seen: &mut HashSet<String>,
    norm_cache: &mut HashMap<(String, String), (Option<String>, Option<String>, Vec<String>)>,
    hier_cache: &mut HashMap<(String, String), (Vec<MethodData>, Option<String>)>,
) {
    let callsite = &callsites[idx];

    let caller_method = match lookup.method_by_id.get(&callsite.caller_method_id) {
        Some(m) => m,
        None => return,
    };
    let caller_class = match lookup.class_by_id.get(&caller_method.class_id) {
        Some(c) => c,
        None => return,
    };

    // Clone these once; they don't change across loop iterations.
    let callee_name = callsite.callee_name.clone();
    let argument_count = callsite.argument_count;
    let argument_types = callsite.argument_types.clone();

    loop {
        // Cycle detection.
        let current_receiver_type_raw = states[idx].receiver_type_raw.clone();
        let cycle_key = format!(
            "{}:{}",
            callsite.id,
            current_receiver_type_raw.as_deref().unwrap_or("")
        );
        if seen.contains(&cycle_key) {
            break;
        }
        seen.insert(cycle_key);

        // Normalize the current receiver type — memoized by (raw_type, caller_class) since
        // imports and package are determined by the caller class and don't vary per-callsite.
        let norm_key = (
            current_receiver_type_raw.clone().unwrap_or_default(),
            caller_class.full_name.clone(),
        );
        let (normalized, norm_source, wildcard_candidates) = if let Some(cached) = norm_cache.get(&norm_key) {
            cached.clone()
        } else {
            let result = normalize_type(
                current_receiver_type_raw.as_deref(),
                &caller_class.imports,
                &caller_class.package_name,
                &lookup.all_class_full_names,
            );
            norm_cache.insert(norm_key, result.clone());
            result
        };
        states[idx].receiver_type_normalized = normalized.clone();
        // Preserve previously-set source (set only on first normalization).
        if states[idx].receiver_resolution_source.is_none() {
            states[idx].receiver_resolution_source = norm_source.clone();
        }

        let mut candidates: Vec<MethodData> = vec![];
        // Set when the hot path has IDs already — skips the MethodData clone at finalise.
        let mut id_candidates: Option<Vec<String>> = None;
        let status;
        let reason;

        if callsite.receiver.is_none() {
            // ---------------------------------------------------------------
            // No receiver: same-class → hierarchy → global name+arity
            // ---------------------------------------------------------------
            let same_class = lookup
                .methods_by_full_class_and_name
                .get(&(caller_method.class_full_name.clone(), callee_name.clone()))
                .cloned()
                .unwrap_or_default();
            let same_class_arity = arity_filter(same_class, argument_count, &argument_types);
            if !same_class_arity.is_empty() {
                let len = same_class_arity.len();
                candidates = same_class_arity;
                if len == 1 {
                    status = "resolved_same_class".to_string();
                    reason = "resolved in caller class by name+arity".to_string();
                } else {
                    status = "ambiguous_overload".to_string();
                    reason = "multiple same-class overloads with same arity".to_string();
                }
            } else {
                let no_recv_hier_key = (caller_method.class_full_name.clone(), callee_name.clone());
                let (inherited, found_in) = if let Some(cached) = hier_cache.get(&no_recv_hier_key) {
                    cached.clone()
                } else {
                    let r = find_method_in_hierarchy(&caller_method.class_full_name, &callee_name, lookup);
                    hier_cache.insert(no_recv_hier_key, r.clone());
                    r
                };
                let inherited_arity = arity_filter(inherited, argument_count, &argument_types);
                if !inherited_arity.is_empty() {
                    let found_label = found_in.as_deref().unwrap_or("?").to_string();
                    candidates = inherited_arity;
                    status = "resolved_inherited".to_string();
                    reason = format!("resolved via inheritance from {}", found_label);
                } else {
                    // Global name+arity fallback — use ID-only maps to avoid cloning large MethodData vecs.
                    let ids: Vec<String> = if argument_count >= 0 {
                        lookup
                            .methods_by_name_arity_ids
                            .get(&(callee_name.clone(), argument_count))
                            .cloned()
                            .unwrap_or_default()
                    } else {
                        lookup
                            .methods_by_name_ids
                            .get(&callee_name)
                            .cloned()
                            .unwrap_or_default()
                    };
                    let len = ids.len();
                    id_candidates = Some(ids);
                    if len == 0 {
                        status = "unresolved_method".to_string();
                        reason = "no method with matching name+arity".to_string();
                    } else if len == 1 {
                        status = "candidate_global_name_arity".to_string();
                        reason = "single global name+arity candidate; not treated as exact because receiver is implicit".to_string();
                    } else {
                        status = "ambiguous_global_name_arity".to_string();
                        reason =
                            "multiple global name+arity candidates; receiver is implicit".to_string();
                    }
                }
            }
        } else {
            // ---------------------------------------------------------------
            // Has receiver
            // ---------------------------------------------------------------
            let receiver = callsite.receiver.as_deref().unwrap();

            if norm_source.as_deref() == Some("import_wildcard") && wildcard_candidates.len() > 1 {
                // Ambiguous wildcard import.
                status = "ambiguous_type".to_string();
                reason = "receiver type matches multiple wildcard imports".to_string();
            } else if let Some(ref norm) = normalized {
                // ---------------------------------------------------------
                // Normalized type is available.
                // ---------------------------------------------------------
                let short_norm = norm.split('.').last().unwrap_or(norm.as_str());

                // Sole implementer check.
                let sole_implementers = lookup
                    .implementers_by_target_short
                    .get(short_norm)
                    .cloned()
                    .unwrap_or_default();
                let mut sole_implementer: Option<String> = None;
                let mut sole_impl_matches: Vec<MethodData> = vec![];
                if sole_implementers.len() == 1 {
                    let impl_class = &sole_implementers[0];
                    let impl_candidates = lookup
                        .methods_by_full_class_and_name
                        .get(&(impl_class.clone(), callee_name.clone()))
                        .cloned()
                        .unwrap_or_default();
                    let filtered =
                        arity_filter(impl_candidates.clone(), argument_count, &argument_types);
                    sole_impl_matches = if !filtered.is_empty() { filtered } else { impl_candidates };
                    if sole_impl_matches.len() == 1 {
                        sole_implementer = Some(impl_class.clone());
                    }
                }

                let full_matches = lookup
                    .methods_by_full_class_and_name
                    .get(&(norm.clone(), callee_name.clone()))
                    .cloned()
                    .unwrap_or_default();

                if let Some(ref impl_class) = sole_implementer {
                    // Prefer the concrete sole implementation.
                    status = "resolved_via_sole_implementation".to_string();
                    reason = format!(
                        "receiver type {} is an interface/abstract class with a single implementer {}",
                        norm, impl_class
                    );
                    candidates = sole_impl_matches;
                } else if !full_matches.is_empty() {
                    let arity_matches =
                        arity_filter(full_matches.clone(), argument_count, &argument_types);
                    candidates = if !arity_matches.is_empty() { arity_matches } else { full_matches };
                    if candidates.len() == 1 {
                        status = match norm_source.as_deref() {
                            Some("import_exact") => "resolved_via_import".to_string(),
                            Some("same_package") => "resolved_same_package".to_string(),
                            _ => "resolved_exact".to_string(),
                        };
                        reason = match norm_source.as_deref() {
                            Some("import_exact") => {
                                "receiver type normalized via exact import".to_string()
                            }
                            Some("same_package") => {
                                "receiver type normalized via same package".to_string()
                            }
                            _ => "resolved by normalized full class name".to_string(),
                        };
                    } else {
                        status = "ambiguous_overload".to_string();
                        reason = "multiple receiver-class overload candidates".to_string();
                    }
                } else {
                    // Walk inheritance chain — memoized by (class, method_name).
                    let (inherited, found_in) = {
                        let hier_key = (norm.clone(), callee_name.clone());
                        if let Some(cached) = hier_cache.get(&hier_key) {
                            cached.clone()
                        } else {
                            let r = find_method_in_hierarchy(norm, &callee_name, lookup);
                            hier_cache.insert(hier_key, r.clone());
                            r
                        }
                    };
                    if !inherited.is_empty() {
                        let arity_matches =
                            arity_filter(inherited.clone(), argument_count, &argument_types);
                        candidates = if !arity_matches.is_empty() { arity_matches } else { inherited };
                        let found_label = found_in.as_deref().unwrap_or("?").to_string();
                        if candidates.len() == 1 {
                            status = "resolved_inherited".to_string();
                            reason = format!("resolved via inheritance from {}", found_label);
                        } else {
                            status = "ambiguous_overload".to_string();
                            reason = "multiple inherited overload candidates".to_string();
                        }
                    } else {
                        // Inner class check: "Outer.Inner" → FQN ending with "Outer$Inner".
                        // Uses precomputed lookup — avoids O(N_classes) linear scan per callsite.
                        let inner_fqn = if norm.contains('.') {
                            let parts: Vec<&str> = norm.split('.').collect();
                            if parts.len() >= 2 {
                                let last2 = &parts[parts.len() - 2..];
                                let key = (last2[0].to_string(), last2[1].to_string());
                                lookup.inner_class_by_outer_inner.get(&key).cloned()
                            } else {
                                None
                            }
                        } else {
                            None
                        };

                        if let Some(ref ifqn) = inner_fqn {
                            let inner_matches = lookup
                                .methods_by_full_class_and_name
                                .get(&(ifqn.clone(), callee_name.clone()))
                                .cloned()
                                .unwrap_or_default();
                            if !inner_matches.is_empty() {
                                let arity_matches = arity_filter(
                                    inner_matches.clone(),
                                    argument_count,
                                    &argument_types,
                                );
                                let final_cands = if !arity_matches.is_empty() {
                                    arity_matches
                                } else {
                                    inner_matches
                                };
                                let (s, r) = if final_cands.len() == 1 {
                                    (
                                        "resolved_exact".to_string(),
                                        format!("resolved inner class via FQN {}", ifqn),
                                    )
                                } else {
                                    (
                                        "ambiguous_overload".to_string(),
                                        "multiple inner class overload candidates".to_string(),
                                    )
                                };
                                // Python has an early return here (skips short_matches).
                                let mut ids: Vec<String> =
                                    final_cands.iter().map(|m| m.id.clone()).collect();
                                ids.sort();
                                states[idx].resolved_candidates = ids;
                                states[idx].candidate_count = final_cands.len();
                                states[idx].resolution_status = s;
                                states[idx].resolution_reason = r;
                                return;
                            }
                        }

                        // Short-class name match fallback.
                        let short_matches = lookup
                            .methods_by_short_class_and_name
                            .get(&(short_norm.to_string(), callee_name.clone()))
                            .cloned()
                            .unwrap_or_default();
                        if !short_matches.is_empty() {
                            let arity_matches =
                                arity_filter(short_matches.clone(), argument_count, &argument_types);
                            candidates =
                                if !arity_matches.is_empty() { arity_matches } else { short_matches };
                            status = "ambiguous_type".to_string();
                            reason = "fallback short-class match only".to_string();
                        } else if lookup.all_class_full_names.contains(norm.as_str()) {
                            status = "unresolved_method".to_string();
                            reason = "receiver class found, method not found".to_string();
                        } else {
                            status = "external_library".to_string();
                            reason =
                                "receiver class not present in indexed codebase".to_string();
                        }
                    }
                }
            } else if receiver.trim() == "this" || receiver.trim() == "super" {
                // ---------------------------------------------------------
                // this/super: set type to caller's class and loop.
                // ---------------------------------------------------------
                states[idx].receiver_type_raw =
                    Some(caller_method.class_full_name.clone());
                states[idx].receiver_resolution_source = Some("this_super".to_string());
                continue;
            } else if receiver.contains('.') && !receiver.trim_end().ends_with(')') {
                // ---------------------------------------------------------
                // Dotted field chain: resolve and loop, or fall back.
                // ---------------------------------------------------------
                let (dotted_type, dotted_source) =
                    resolve_dotted_receiver(receiver, caller_method, caller_class, lookup);
                if let Some(dt) = dotted_type {
                    states[idx].receiver_type_raw = Some(dt);
                    states[idx].receiver_resolution_source = dotted_source;
                    continue;
                } else {
                    let (s, r, c) = interface_dispatch_fallback(
                        &callee_name,
                        argument_count,
                        &argument_types,
                        "unresolved_receiver",
                        "could not infer or normalize receiver type",
                        &candidates,
                        lookup,
                    );
                    status = s;
                    reason = r;
                    candidates = c;
                }
            } else {
                // ---------------------------------------------------------
                // Last resort: interface dispatch fallback.
                // ---------------------------------------------------------
                let (s, r, c) = interface_dispatch_fallback(
                    &callee_name,
                    argument_count,
                    &argument_types,
                    "unresolved_receiver",
                    "could not infer or normalize receiver type",
                    &candidates,
                    lookup,
                );
                status = s;
                reason = r;
                candidates = c;
            }
        }

        // Finalise this iteration (no continue above means we've settled).
        let (ids, count) = if let Some(precomputed_ids) = id_candidates {
            let len = precomputed_ids.len();
            (precomputed_ids, len)
        } else {
            let mut ids: Vec<String> = candidates.iter().map(|m| m.id.clone()).collect();
            ids.sort();
            let len = ids.len();
            (ids, len)
        };
        states[idx].resolved_candidates = ids;
        states[idx].candidate_count = count;
        states[idx].resolution_status = status;
        states[idx].resolution_reason = reason;
        break;
    }
}

// ---------------------------------------------------------------------------
// Sub-functions
// ---------------------------------------------------------------------------

/// BFS over extends + implements to find a method not declared on the direct
/// receiver type. Returns (candidates, found_in_class).
fn find_method_in_hierarchy(
    normalized_class: &str,
    callee_name: &str,
    lookup: &LookupTables,
) -> (Vec<MethodData>, Option<String>) {
    let mut visited: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<String> = VecDeque::new();
    queue.push_back(normalized_class.to_string());

    while let Some(current) = queue.pop_front() {
        if visited.contains(&current) {
            continue;
        }
        visited.insert(current.clone());

        let matches = lookup
            .methods_by_full_class_and_name
            .get(&(current.clone(), callee_name.to_string()))
            .cloned()
            .unwrap_or_default();
        if !matches.is_empty() {
            return (matches, Some(current));
        }

        let cls = match lookup.class_by_full_name.get(&current) {
            Some(c) => c,
            None => continue,
        };

        let mut parents: Vec<String> = vec![];
        if let Some(ref ext) = cls.extends {
            parents.push(ext.clone());
        }
        parents.extend(cls.implements.clone());

        for raw_parent in parents {
            let candidate = resolve_parent_name(&raw_parent, cls, lookup);
            if lookup.all_class_full_names.contains(&candidate) && !visited.contains(&candidate) {
                queue.push_back(candidate);
            }
        }
    }

    (vec![], None)
}

/// Like `find_method_in_hierarchy` but also tries short-name lookup for
/// parents whose FQN can't be resolved (covers "extends BaseRepository" without
/// an explicit import).
fn find_method_relaxed(
    normalized_class: &str,
    callee_name: &str,
    argument_count: i64,
    lookup: &LookupTables,
) -> (Vec<MethodData>, Option<String>) {
    let mut visited: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<String> = VecDeque::new();
    queue.push_back(normalized_class.to_string());

    while let Some(current) = queue.pop_front() {
        if visited.contains(&current) {
            continue;
        }
        visited.insert(current.clone());

        let matches = lookup
            .methods_by_full_class_and_name
            .get(&(current.clone(), callee_name.to_string()))
            .cloned()
            .unwrap_or_default();
        if !matches.is_empty() {
            if argument_count >= 0 {
                let arity: Vec<MethodData> = matches
                    .iter()
                    .filter(|m| m.parameter_types.len() == argument_count as usize)
                    .cloned()
                    .collect();
                return (if !arity.is_empty() { arity } else { matches }, Some(current));
            }
            return (matches, Some(current));
        }

        let cls = match lookup.class_by_full_name.get(&current) {
            Some(c) => c,
            None => continue,
        };

        let mut parents: Vec<String> = vec![];
        if let Some(ref ext) = cls.extends {
            parents.push(ext.clone());
        }
        parents.extend(cls.implements.clone());

        for raw_parent in parents {
            let candidate = resolve_parent_name(&raw_parent, cls, lookup);
            if lookup.all_class_full_names.contains(&candidate) && !visited.contains(&candidate) {
                queue.push_back(candidate);
                continue;
            }
            // Fallback: short-name match for unresolvable FQNs.
            let short = raw_parent.split('.').last().unwrap_or(&raw_parent);
            let short = short.split('<').next().unwrap_or(short);
            let short_hits = lookup
                .methods_by_short_class_and_name
                .get(&(short.to_string(), callee_name.to_string()))
                .cloned()
                .unwrap_or_default();
            if !short_hits.is_empty() {
                if argument_count >= 0 {
                    let arity: Vec<MethodData> = short_hits
                        .iter()
                        .filter(|m| m.parameter_types.len() == argument_count as usize)
                        .cloned()
                        .collect();
                    return (
                        if !arity.is_empty() { arity } else { short_hits },
                        Some(format!("~{}", short)),
                    );
                }
                return (short_hits, Some(format!("~{}", short)));
            }
        }
    }

    (vec![], None)
}

/// Resolve a raw parent name (from `extends`/`implements`) to a FQN using the
/// parent class's own package and imports.
fn resolve_parent_name(raw_parent: &str, cls: &ClassData, lookup: &LookupTables) -> String {
    if raw_parent.contains('.') {
        raw_parent.to_string()
    } else if !cls.package_name.is_empty() {
        let pkg_candidate = format!("{}.{}", cls.package_name, raw_parent);
        if lookup.all_class_full_names.contains(&pkg_candidate) {
            pkg_candidate
        } else {
            let (norm, _, _) = normalize_type(
                Some(raw_parent),
                &cls.imports,
                &cls.package_name,
                &lookup.all_class_full_names,
            );
            norm.unwrap_or_else(|| raw_parent.to_string())
        }
    } else {
        raw_parent.to_string()
    }
}

/// Resolve a dotted receiver expression (`this.repo`, `svc.helper`, `a.b.c`)
/// by walking field types segment by segment. Returns (resolved_type, source).
fn resolve_dotted_receiver(
    receiver_text: &str,
    caller_method: &MethodData,
    caller_class: &ClassData,
    lookup: &LookupTables,
) -> (Option<String>, Option<String>) {
    let parts: Vec<&str> = receiver_text.split('.').collect();
    if parts.len() < 2 {
        return (None, None);
    }

    let first = parts[0];
    let params_map: HashMap<&str, &str> = caller_method
        .parameter_names
        .iter()
        .zip(caller_method.parameter_types.iter())
        .map(|(n, t)| (n.as_str(), t.as_str()))
        .collect();
    let local_types = &caller_method.local_variable_types;

    let mut current_type: Option<String> = if first == "this" {
        Some(caller_class.full_name.clone())
    } else {
        let raw = local_types
            .get(first)
            .map(|s| s.as_str())
            .or_else(|| params_map.get(first).copied())
            .or_else(|| {
                lookup
                    .fields_by_class
                    .get(&caller_class.full_name)
                    .and_then(|fields| fields.get(first))
                    .map(|s| s.as_str())
            });
        match raw {
            Some(r) => {
                let (norm, _, _) = normalize_type(
                    Some(r),
                    &caller_class.imports,
                    &caller_class.package_name,
                    &lookup.all_class_full_names,
                );
                norm
            }
            None => return (None, None),
        }
    };

    if current_type.is_none() {
        return (None, None);
    }

    for part in &parts[1..] {
        let ct = current_type.as_ref().unwrap().clone();
        let owner = match lookup.class_by_full_name.get(&ct) {
            Some(o) => o,
            None => {
                // External type — return current_type so caller sees external_library.
                return (current_type, Some("dotted_chain_external".to_string()));
            }
        };

        // Walk superclass chain to find the field (may be inherited).
        let mut raw_field_type: Option<String> = None;
        let mut walk_type: Option<String> = Some(ct.clone());
        loop {
            let wt = match walk_type {
                Some(ref w) => w.clone(),
                None => break,
            };
            if let Some(ft) = lookup
                .fields_by_class
                .get(&wt)
                .and_then(|f| f.get(*part))
            {
                raw_field_type = Some(ft.clone());
                break;
            }
            // Climb to superclass.
            walk_type = match lookup.class_by_full_name.get(&wt) {
                Some(wc) => match wc.extends.as_deref() {
                    Some(ext) => {
                        let (wt_norm, _, _) = normalize_type(
                            Some(ext),
                            &wc.imports,
                            &wc.package_name,
                            &lookup.all_class_full_names,
                        );
                        wt_norm
                    }
                    None => None,
                },
                None => None,
            };
        }

        match raw_field_type {
            None => return (None, None),
            Some(rft) => {
                let (norm, _, _) = normalize_type(
                    Some(&rft),
                    &owner.imports,
                    &owner.package_name,
                    &lookup.all_class_full_names,
                );
                if norm.is_none() {
                    return (None, None);
                }
                current_type = norm;
            }
        }
    }

    (current_type, Some("dotted_field_chain".to_string()))
}

/// Interface dispatch fallback: when receiver is unresolved, find the sole
/// concrete implementer that has a matching method.
fn interface_dispatch_fallback(
    callee_name: &str,
    argument_count: i64,
    _argument_types: &[Option<String>],
    default_status: &str,
    default_reason: &str,
    default_candidates: &[MethodData],
    lookup: &LookupTables,
) -> (String, String, Vec<MethodData>) {
    // Use precomputed impl_methods_by_name_arity — O(1) lookup instead of O(all_impls).
    let candidates = if argument_count >= 0 {
        lookup
            .impl_methods_by_name_arity
            .get(&(callee_name.to_string(), argument_count))
            .cloned()
            .unwrap_or_default()
    } else {
        // Collect across all arities for this callee name.
        lookup
            .impl_methods_by_name_arity
            .iter()
            .filter(|((name, _), _)| name == callee_name)
            .flat_map(|(_, v)| v.iter().cloned())
            .collect()
    };

    if candidates.len() == 1 {
        return (
            "resolved_interface_dispatch_fallback".to_string(),
            "sole impl match on callee name+arity across all implementers".to_string(),
            candidates,
        );
    }

    (
        default_status.to_string(),
        default_reason.to_string(),
        default_candidates.to_vec(),
    )
}

/// Arity filter followed by argument-type narrowing.
fn arity_filter(
    methods: Vec<MethodData>,
    argument_count: i64,
    argument_types: &[Option<String>],
) -> Vec<MethodData> {
    if argument_count < 0 {
        return methods;
    }
    let arity_matches: Vec<MethodData> = methods
        .into_iter()
        .filter(|m| m.parameter_types.len() == argument_count as usize)
        .collect();
    type_filter(arity_matches, argument_types, argument_count)
}

/// Narrow arity-matched candidates using inferred argument types when available.
/// Falls back to the arity-only list if type filtering would empty the result.
fn type_filter(
    methods: Vec<MethodData>,
    argument_types: &[Option<String>],
    argument_count: i64,
) -> Vec<MethodData> {
    if argument_count < 0 || argument_types.len() != argument_count as usize {
        return methods;
    }
    if !argument_types.iter().any(|t| t.is_some()) {
        return methods;
    }

    let narrowed: Vec<MethodData> = methods
        .iter()
        .filter(|m| {
            for (i, arg_type) in argument_types.iter().enumerate() {
                if let Some(at) = arg_type {
                    if i >= m.parameter_types.len() {
                        return false;
                    }
                    let a_short = strip_generic(at)
                        .split('.')
                        .last()
                        .unwrap_or(at.as_str())
                        .to_string();
                    let p_short = strip_generic(&m.parameter_types[i])
                        .split('.')
                        .last()
                        .unwrap_or(m.parameter_types[i].as_str())
                        .to_string();
                    if a_short != p_short {
                        return false;
                    }
                }
            }
            true
        })
        .cloned()
        .collect();

    if narrowed.is_empty() { methods } else { narrowed }
}

/// BFS to collect all concrete subtypes of a given FQN (for CHA).
fn cha_subtypes(fqn: &str, lookup: &LookupTables) -> Vec<String> {
    let mut visited: HashSet<String> = HashSet::new();
    let short = fqn.split('.').last().unwrap_or(fqn);

    let mut stack: Vec<String> = lookup
        .implementers_by_fqn
        .get(fqn)
        .cloned()
        .unwrap_or_default();
    if short != fqn {
        stack.extend(
            lookup
                .implementers_by_fqn
                .get(short)
                .cloned()
                .unwrap_or_default(),
        );
    }

    let mut result: Vec<String> = vec![];
    while let Some(cls) = stack.pop() {
        if visited.contains(&cls) {
            continue;
        }
        visited.insert(cls.clone());
        result.push(cls.clone());
        stack.extend(
            lookup
                .implementers_by_fqn
                .get(&cls)
                .cloned()
                .unwrap_or_default(),
        );
        let short_cls = cls.split('.').last().unwrap_or(&cls);
        if short_cls != cls.as_str() {
            stack.extend(
                lookup
                    .implementers_by_fqn
                    .get(short_cls)
                    .cloned()
                    .unwrap_or_default(),
            );
        }
    }

    result
}
