use pyo3::prelude::*;
use pyo3::types::PySequence;
use std::collections::HashMap;

macro_rules! from_seq {
    ($name:ident { $($field:ident : $idx:literal => $ty:ty),* $(,)? }) => {
        impl<'a, 'py> FromPyObject<'a, 'py> for $name {
            type Error = PyErr;
            fn extract(obj: pyo3::Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
                let s = obj.cast::<PySequence>().map_err(PyErr::from)?.to_owned();
                Ok(Self {
                    $($field: s.get_item($idx)?.extract::<$ty>()?,)*
                })
            }
        }
    };
}

#[derive(Debug, Clone)]
pub struct MethodData {
    pub id: String,
    pub class_id: String,
    pub class_full_name: String,
    pub method_name: String,
    pub return_type: String,
    pub parameter_types: Vec<String>,
    pub parameter_names: Vec<String>,
    pub file_path: String,
    pub language: String,
    pub local_variable_types: HashMap<String, String>,
    pub field_reads: Vec<String>,
}

from_seq!(MethodData {
    id: 0 => String,
    class_id: 1 => String,
    class_full_name: 2 => String,
    method_name: 3 => String,
    return_type: 4 => String,
    parameter_types: 5 => Vec<String>,
    parameter_names: 6 => Vec<String>,
    file_path: 7 => String,
    language: 8 => String,
    local_variable_types: 9 => HashMap<String, String>,
    field_reads: 10 => Vec<String>,
});

#[derive(Debug, Clone)]
pub struct ClassData {
    pub id: String,
    pub full_name: String,
    pub package_name: String,
    pub file_path: String,
    pub stereotypes: Vec<String>,
    pub implements: Vec<String>,
    pub extends: Option<String>,
    pub imports: Vec<String>,
}

impl<'a, 'py> FromPyObject<'a, 'py> for ClassData {
    type Error = PyErr;
    fn extract(obj: pyo3::Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let s = obj.cast::<PySequence>().map_err(PyErr::from)?.to_owned();
        let extends_raw: String = s.get_item(6)?.extract()?;
        Ok(Self {
            id: s.get_item(0)?.extract()?,
            full_name: s.get_item(1)?.extract()?,
            package_name: s.get_item(2)?.extract()?,
            file_path: s.get_item(3)?.extract()?,
            stereotypes: s.get_item(4)?.extract()?,
            implements: s.get_item(5)?.extract()?,
            extends: if extends_raw.is_empty() { None } else { Some(extends_raw) },
            imports: s.get_item(7)?.extract()?,
        })
    }
}

#[derive(Debug, Clone)]
pub struct CallSiteData {
    pub id: String,
    pub caller_method_id: String,
    pub callee_name: String,
    pub receiver: Option<String>,
    pub receiver_type_raw: Option<String>,
    pub argument_count: i64,
    pub argument_types: Vec<Option<String>>,
    pub text: String,
    pub file_path: String,
}

impl<'a, 'py> FromPyObject<'a, 'py> for CallSiteData {
    type Error = PyErr;
    fn extract(obj: pyo3::Borrowed<'a, 'py, PyAny>) -> Result<Self, Self::Error> {
        let s = obj.cast::<PySequence>().map_err(PyErr::from)?.to_owned();
        let receiver_raw: Option<String> = s.get_item(3)?.extract()?;
        let rtype_raw: String = s.get_item(4)?.extract()?;
        Ok(Self {
            id: s.get_item(0)?.extract()?,
            caller_method_id: s.get_item(1)?.extract()?,
            callee_name: s.get_item(2)?.extract()?,
            receiver: receiver_raw.filter(|r| !r.is_empty()),
            receiver_type_raw: if rtype_raw.is_empty() { None } else { Some(rtype_raw) },
            argument_count: s.get_item(5)?.extract()?,
            argument_types: s.get_item(6)?.extract()?,
            text: s.get_item(7)?.extract()?,
            file_path: s.get_item(8)?.extract()?,
        })
    }
}

#[derive(Debug, Clone)]
pub struct EdgeData {
    pub source_class: String,
    pub target_class: String,
    pub relation: String,
}

from_seq!(EdgeData {
    source_class: 0 => String,
    target_class: 1 => String,
    relation: 2 => String,
});

#[derive(Debug, Clone)]
pub struct FieldData {
    pub class_id: String,
    pub name: String,
    pub type_name: String,
}

from_seq!(FieldData {
    class_id: 0 => String,
    name: 1 => String,
    type_name: 2 => String,
});

#[derive(Debug, Clone)]
pub struct LinePatch {
    pub method_id: String,
    pub new_start_line: i64,
    pub new_end_line: i64,
    pub new_source: String,
}

from_seq!(LinePatch {
    method_id: 0 => String,
    new_start_line: 1 => i64,
    new_end_line: 2 => i64,
    new_source: 3 => String,
});

#[pyclass]
#[derive(Debug, Clone)]
pub struct ResolveStats {
    #[pyo3(get)]
    pub total_callsites: usize,
    #[pyo3(get)]
    pub resolved: usize,
    #[pyo3(get)]
    pub unresolved: usize,
    #[pyo3(get)]
    pub external_library: usize,
    #[pyo3(get)]
    pub duration_ms: u64,
}

// Internal — not exposed to Python.
#[derive(Debug, Clone)]
pub struct ResolvedCallSite {
    pub id: String,
    pub caller_method_id: String,
    pub resolution_status: String,
    pub resolution_reason: String,
    pub resolved_candidates: Vec<String>,
    pub candidate_count: usize,
    pub receiver_type_normalized: Option<String>,
    pub receiver_resolution_source: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ResolvedCallEdge {
    pub id: String,
    pub callsite_id: String,
    pub caller_method_id: String,
    pub callee_method_id: String,
}
