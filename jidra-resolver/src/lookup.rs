use std::collections::{HashMap, HashSet};

use crate::models::{CallSiteData, ClassData, EdgeData, FieldData, MethodData};

pub struct LookupTables {
    pub class_by_id: HashMap<String, ClassData>,
    pub class_by_full_name: HashMap<String, ClassData>,
    pub method_by_id: HashMap<String, MethodData>,

    // (class_full_name, method_name) -> Vec<MethodData>
    pub methods_by_full_class_and_name: HashMap<(String, String), Vec<MethodData>>,
    // (class_short_name, method_name) -> Vec<MethodData>
    pub methods_by_short_class_and_name: HashMap<(String, String), Vec<MethodData>>,
    // (method_name, arity) -> Vec<MethodData>
    pub methods_by_name_arity: HashMap<(String, i64), Vec<MethodData>>,
    // method_name -> Vec<MethodData>
    pub methods_by_name: HashMap<String, Vec<MethodData>>,
    // class_full_name -> Vec<MethodData>
    pub methods_by_full_class: HashMap<String, Vec<MethodData>>,

    // class_full_name -> field_name -> type_name
    pub fields_by_class: HashMap<String, HashMap<String, String>>,

    // interface short name -> Vec<concrete implementer FQNs>
    pub implementers_by_target_short: HashMap<String, Vec<String>>,

    // (caller_method_id, callsite_text) -> index into callsites Vec
    pub chain_index: HashMap<(String, String), usize>,

    // union of all class FQNs + method class FQNs
    pub all_class_full_names: HashSet<String>,

    // class_short_name -> Vec<FQN>
    pub class_by_short_name: HashMap<String, Vec<String>>,

    // FQN -> Vec<subtype FQNs> (for CHA)
    pub implementers_by_fqn: HashMap<String, Vec<String>>,

    // All concrete implementer FQNs (union across all interfaces) — precomputed for interface_dispatch_fallback.
    pub all_implementer_fqns: HashSet<String>,

    // (method_name, arity) -> Vec<MethodData> restricted to implementer classes — for interface_dispatch_fallback.
    pub impl_methods_by_name_arity: HashMap<(String, i64), Vec<MethodData>>,

    // ID-only variants for large global buckets — avoids expensive MethodData clone on hot path.
    pub methods_by_name_arity_ids: HashMap<(String, i64), Vec<String>>,
    pub methods_by_name_ids: HashMap<String, Vec<String>>,

    // (outer_short, inner_short) -> FQN for inner class lookups.
    // Replaces the O(N_classes) linear scan in the inner-class fallback path.
    pub inner_class_by_outer_inner: HashMap<(String, String), String>,
}

impl LookupTables {
    pub fn build(
        methods: &[MethodData],
        classes: &[ClassData],
        fields: &[FieldData],
        inheritance_edges: &[EdgeData],
        callsites: &[CallSiteData],
    ) -> Self {
        // --- class_by_id and class_by_full_name ---
        let class_by_id: HashMap<String, ClassData> = classes
            .iter()
            .map(|c| (c.id.clone(), c.clone()))
            .collect();

        let class_by_full_name: HashMap<String, ClassData> = classes
            .iter()
            .map(|c| (c.full_name.clone(), c.clone()))
            .collect();

        // --- method_by_id ---
        let method_by_id: HashMap<String, MethodData> = methods
            .iter()
            .map(|m| (m.id.clone(), m.clone()))
            .collect();

        // --- fields_by_class ---
        let mut fields_by_class: HashMap<String, HashMap<String, String>> = HashMap::new();
        for f in fields {
            if let Some(owner) = class_by_id.get(&f.class_id) {
                fields_by_class
                    .entry(owner.full_name.clone())
                    .or_default()
                    .insert(f.name.clone(), f.type_name.clone());
            }
        }

        // --- five method lookup maps ---
        let mut methods_by_full_class_and_name: HashMap<(String, String), Vec<MethodData>> =
            HashMap::new();
        let mut methods_by_short_class_and_name: HashMap<(String, String), Vec<MethodData>> =
            HashMap::new();
        let mut methods_by_name_arity: HashMap<(String, i64), Vec<MethodData>> = HashMap::new();
        let mut methods_by_name: HashMap<String, Vec<MethodData>> = HashMap::new();
        let mut methods_by_full_class: HashMap<String, Vec<MethodData>> = HashMap::new();

        for m in methods {
            let short_class = m
                .class_full_name
                .split('.')
                .last()
                .unwrap_or(&m.class_full_name)
                .to_string();
            let arity = m.parameter_types.len() as i64;

            methods_by_full_class_and_name
                .entry((m.class_full_name.clone(), m.method_name.clone()))
                .or_default()
                .push(m.clone());

            methods_by_short_class_and_name
                .entry((short_class, m.method_name.clone()))
                .or_default()
                .push(m.clone());

            methods_by_name_arity
                .entry((m.method_name.clone(), arity))
                .or_default()
                .push(m.clone());

            methods_by_name
                .entry(m.method_name.clone())
                .or_default()
                .push(m.clone());

            methods_by_full_class
                .entry(m.class_full_name.clone())
                .or_default()
                .push(m.clone());
        }

        // Sort each bucket by id ascending (mirrors Python's sorted(..., key=lambda x: x.id))
        for bucket in methods_by_full_class_and_name.values_mut() {
            bucket.sort_by(|a, b| a.id.cmp(&b.id));
        }
        for bucket in methods_by_short_class_and_name.values_mut() {
            bucket.sort_by(|a, b| a.id.cmp(&b.id));
        }
        for bucket in methods_by_name_arity.values_mut() {
            bucket.sort_by(|a, b| a.id.cmp(&b.id));
        }
        for bucket in methods_by_name.values_mut() {
            bucket.sort_by(|a, b| a.id.cmp(&b.id));
        }
        for bucket in methods_by_full_class.values_mut() {
            bucket.sort_by(|a, b| a.id.cmp(&b.id));
        }

        // --- all_class_full_names ---
        let mut all_class_full_names: HashSet<String> = methods_by_full_class.keys().cloned().collect();
        for c in classes {
            all_class_full_names.insert(c.full_name.clone());
        }

        // --- implementers_by_target_short (implements only) ---
        let mut implementers_by_target_short_set: HashMap<String, HashSet<String>> = HashMap::new();
        for edge in inheritance_edges {
            if edge.relation != "implements" {
                continue;
            }
            let key = edge
                .target_class
                .split('.')
                .last()
                .unwrap_or(&edge.target_class)
                .to_string();
            implementers_by_target_short_set
                .entry(key)
                .or_default()
                .insert(edge.source_class.clone());
        }
        let implementers_by_target_short: HashMap<String, Vec<String>> =
            implementers_by_target_short_set
                .into_iter()
                .map(|(k, v)| (k, v.into_iter().collect()))
                .collect();

        // --- chain_index ---
        let chain_index: HashMap<(String, String), usize> = callsites
            .iter()
            .enumerate()
            .map(|(i, c)| ((c.caller_method_id.clone(), c.text.clone()), i))
            .collect();

        // --- class_by_short_name ---
        let mut class_by_short_name: HashMap<String, Vec<String>> = HashMap::new();
        for c in classes {
            let short = c
                .full_name
                .split('.')
                .last()
                .unwrap_or(&c.full_name)
                .to_string();
            class_by_short_name
                .entry(short)
                .or_default()
                .push(c.full_name.clone());
        }

        // --- implementers_by_fqn (implements + extends, keyed by FQN and short name) ---
        let mut implementers_by_fqn_set: HashMap<String, HashSet<String>> = HashMap::new();
        for edge in inheritance_edges {
            if edge.relation != "implements" && edge.relation != "extends" {
                continue;
            }
            let short_target = edge
                .target_class
                .split('.')
                .last()
                .unwrap_or(&edge.target_class)
                .to_string();
            for key in [edge.target_class.clone(), short_target] {
                implementers_by_fqn_set
                    .entry(key)
                    .or_default()
                    .insert(edge.source_class.clone());
            }
        }
        let implementers_by_fqn: HashMap<String, Vec<String>> = implementers_by_fqn_set
            .into_iter()
            .map(|(k, v)| (k, v.into_iter().collect()))
            .collect();

        // --- ID-only maps for hot global-name-arity path ---
        let mut methods_by_name_arity_ids: HashMap<(String, i64), Vec<String>> = HashMap::new();
        let mut methods_by_name_ids: HashMap<String, Vec<String>> = HashMap::new();
        for m in methods {
            let arity = m.parameter_types.len() as i64;
            methods_by_name_arity_ids
                .entry((m.method_name.clone(), arity))
                .or_default()
                .push(m.id.clone());
            methods_by_name_ids
                .entry(m.method_name.clone())
                .or_default()
                .push(m.id.clone());
        }
        for ids in methods_by_name_arity_ids.values_mut() {
            ids.sort();
        }
        for ids in methods_by_name_ids.values_mut() {
            ids.sort();
        }

        // --- inner_class_by_outer_inner: (outer_short, inner_short) -> FQN ---
        // Precomputed from all_class_full_names to replace the O(N) linear scan.
        // Handles both "Outer$Inner" (bytecode) and ".Outer.Inner" (source/nested) patterns.
        // Mirrors the original search: ends_with("$Outer$Inner") or ends_with(".Outer.Inner").
        let mut inner_class_by_outer_inner: HashMap<(String, String), String> = HashMap::new();
        for fqn in all_class_full_names.iter() {
            // Dollar-notation: "com.example.Outer$Inner" → key ("Outer", "Inner")
            if let Some(dollar_pos) = fqn.rfind('$') {
                let inner = &fqn[dollar_pos + 1..];
                if !inner.contains('$') && !inner.contains('.') {
                    let prefix = &fqn[..dollar_pos];
                    // outer is last component before the final '$'
                    let outer = prefix.split('.').last().unwrap_or(prefix);
                    let outer = outer.split('$').last().unwrap_or(outer);
                    // Emulate original: searched for "$Outer$Inner" suffix, so norm was "Outer.Inner" → parts ["Outer","Inner"]
                    inner_class_by_outer_inner
                        .entry((outer.to_string(), inner.to_string()))
                        .or_insert_with(|| fqn.clone());
                }
            }
            // Dot-notation: ".com.example.Outer.Inner" → key ("Outer", "Inner") via ends_with(".Outer.Inner")
            // Last two dot-separated components form the key (same as original's last2).
            {
                let parts: Vec<&str> = fqn.split('.').collect();
                if parts.len() >= 3 {
                    // Only treat as inner class if FQN has at least 3 parts (package + outer + inner).
                    let outer = parts[parts.len() - 2];
                    let inner = parts[parts.len() - 1];
                    // Register all (second_last, last) pairs — mirrors original ends_with(".Outer.Inner") scan.
                    // Dollar-notation entries added above take priority via or_insert_with.
                    inner_class_by_outer_inner
                        .entry((outer.to_string(), inner.to_string()))
                        .or_insert_with(|| fqn.clone());
                }
            }
        }

        // --- all_implementer_fqns + impl_methods_by_name_arity ---
        let all_implementer_fqns: HashSet<String> = implementers_by_target_short
            .values()
            .flat_map(|v| v.iter().cloned())
            .collect();

        let mut impl_methods_by_name_arity: HashMap<(String, i64), Vec<MethodData>> = HashMap::new();
        for m in methods {
            if all_implementer_fqns.contains(&m.class_full_name) {
                let arity = m.parameter_types.len() as i64;
                impl_methods_by_name_arity
                    .entry((m.method_name.clone(), arity))
                    .or_default()
                    .push(m.clone());
            }
        }

        Self {
            class_by_id,
            class_by_full_name,
            method_by_id,
            methods_by_full_class_and_name,
            methods_by_short_class_and_name,
            methods_by_name_arity,
            methods_by_name,
            methods_by_full_class,
            fields_by_class,
            implementers_by_target_short,
            chain_index,
            all_class_full_names,
            class_by_short_name,
            implementers_by_fqn,
            all_implementer_fqns,
            impl_methods_by_name_arity,
            methods_by_name_arity_ids,
            methods_by_name_ids,
            inner_class_by_outer_inner,
        }
    }
}
