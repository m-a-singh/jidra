use once_cell::sync::Lazy;
use std::collections::HashSet;

/// Java types implicitly available via java.lang — no import needed.
pub static JAVA_LANG_TYPES: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    let mut s = HashSet::new();
    s.insert("String");
    s.insert("Integer");
    s.insert("Boolean");
    s.insert("Long");
    s.insert("Double");
    s.insert("Float");
    s.insert("Object");
    s
});

/// Remove type arguments so `List<MyService>` becomes `List`.
pub fn strip_generic(type_str: &str) -> String {
    match type_str.find('<') {
        Some(idx) => type_str[..idx].trim().to_string(),
        None => type_str.to_string(),
    }
}

/// Resolve a raw (possibly unqualified) type name to a fully-qualified class name.
///
/// Returns `(normalized, source_label, wildcard_candidates)`.
///
/// - `normalized`           — the resolved FQCN, or `None` when ambiguous (multiple wildcard hits)
/// - `source_label`         — one of "fqcn", "import_exact", "static_import", "same_package",
///                            "import_wildcard", "java_lang", "unqualified", or `None`
/// - `wildcard_candidates`  — all matching FQCN candidates (length > 1 when ambiguous)
pub fn normalize_type(
    raw_type: Option<&str>,
    caller_imports: &[String],
    caller_package: &str,
    all_class_full_names: &HashSet<String>,
) -> (Option<String>, Option<String>, Vec<String>) {
    let raw_type = match raw_type {
        None => return (None, None, vec![]),
        Some(t) => t,
    };

    // Strip generics before any lookup so 'List<Foo>' resolves as 'List'.
    let raw_type = strip_generic(raw_type);
    let raw_type = raw_type.as_str();

    // Already fully qualified.
    if raw_type.contains('.') {
        return (
            Some(raw_type.to_string()),
            Some("fqcn".to_string()),
            vec![raw_type.to_string()],
        );
    }

    let short = raw_type;

    // Exact import match (skip wildcards and static imports).
    for imp in caller_imports {
        if imp.ends_with(".*") || imp.starts_with("static ") {
            continue;
        }
        if imp.split('.').last() == Some(short) {
            return (
                Some(imp.clone()),
                Some("import_exact".to_string()),
                vec![imp.clone()],
            );
        }
    }

    // Static import: `static com.example.Foo.METHOD` → owner is `com.example.Foo`.
    for imp in caller_imports {
        if !imp.starts_with("static ") {
            continue;
        }
        let without_static = imp.replacen("static ", "", 1);
        let parts: Vec<&str> = without_static.split('.').collect();
        if parts.last() == Some(&short) {
            let owner = parts[..parts.len() - 1].join(".");
            return (
                Some(owner.clone()),
                Some("static_import".to_string()),
                vec![owner],
            );
        }
    }

    // Same-package lookup.
    let same_pkg = if caller_package.is_empty() {
        short.to_string()
    } else {
        format!("{}.{}", caller_package, short)
    };
    if all_class_full_names.contains(&same_pkg) {
        return (
            Some(same_pkg.clone()),
            Some("same_package".to_string()),
            vec![same_pkg],
        );
    }

    // Wildcard import candidates.
    let mut wildcard_candidates: Vec<String> = Vec::new();
    for imp in caller_imports {
        if !imp.ends_with(".*") {
            continue;
        }
        let prefix = &imp[..imp.len() - 2]; // strip ".*"
        let candidate = format!("{}.{}", prefix, short);
        if all_class_full_names.contains(&candidate) {
            wildcard_candidates.push(candidate);
        }
    }
    if !wildcard_candidates.is_empty() {
        let mut unique: Vec<String> = wildcard_candidates.clone();
        unique.sort();
        unique.dedup();
        let normalized = if unique.len() == 1 {
            Some(unique[0].clone())
        } else {
            None
        };
        return (normalized, Some("import_wildcard".to_string()), unique);
    }

    // java.lang implicit imports.
    if JAVA_LANG_TYPES.contains(short) {
        let fqcn = format!("java.lang.{}", short);
        return (Some(fqcn.clone()), Some("java_lang".to_string()), vec![fqcn]);
    }

    // Unqualified fallback — return as-is.
    (
        Some(short.to_string()),
        Some("unqualified".to_string()),
        vec![short.to_string()],
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strip_generic_with_type_arg() {
        assert_eq!(strip_generic("List<MyService>"), "List");
    }

    #[test]
    fn test_strip_generic_no_type_arg() {
        assert_eq!(strip_generic("String"), "String");
    }

    #[test]
    fn test_normalize_fqcn() {
        let classes = HashSet::new();
        let (norm, label, cands) = normalize_type(
            Some("com.example.Foo"),
            &[],
            "com.example",
            &classes,
        );
        assert_eq!(norm.unwrap(), "com.example.Foo");
        assert_eq!(label.unwrap(), "fqcn");
        assert_eq!(cands, vec!["com.example.Foo"]);
    }

    #[test]
    fn test_normalize_java_lang() {
        let classes = HashSet::new();
        let (norm, label, _) =
            normalize_type(Some("String"), &[], "com.example", &classes);
        assert_eq!(norm.unwrap(), "java.lang.String");
        assert_eq!(label.unwrap(), "java_lang");
    }

    #[test]
    fn test_normalize_exact_import() {
        let classes = HashSet::new();
        let imports = vec!["com.example.MyService".to_string()];
        let (norm, label, _) =
            normalize_type(Some("MyService"), &imports, "com.other", &classes);
        assert_eq!(norm.unwrap(), "com.example.MyService");
        assert_eq!(label.unwrap(), "import_exact");
    }
}
