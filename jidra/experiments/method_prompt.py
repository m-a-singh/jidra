from __future__ import annotations

import json


def build_method_analysis_prompt(input) -> str:
    method = input.method
    cls = input.class_entry

    signature = method.get("signature", "")
    class_full_name = cls.get("full_name", "")
    class_annotations = cls.get("annotations", [])
    parameters = list(zip(method.get("parameter_names", []), method.get("parameter_types", [])))
    field_reads = input.field_reads
    field_writes = input.field_writes
    local_variables = sorted(input.local_variable_types.items(), key=lambda x: x[0])

    call_lines: list[str] = []
    for call in sorted(
        input.callsites, key=lambda c: (c.get("line", 0), c.get("column", 0), c.get("id", ""))
    ):
        call_lines.append(
            json.dumps(
                {
                    "id": call.get("id"),
                    "callee_name": call.get("callee_name"),
                    "receiver": call.get("receiver"),
                    "receiver_type_normalized": call.get("receiver_type_normalized"),
                    "argument_count": call.get("argument_count"),
                    "resolution_status": call.get("resolution_status"),
                    "resolution_reason": call.get("resolution_reason"),
                    "candidate_count": call.get("candidate_count"),
                },
                ensure_ascii=True,
            )
        )

    def _fmt_lines(items: list[str]) -> str:
        return "\n".join(items) if items else "(none)"

    def _fmt_param_lines() -> str:
        if not parameters:
            return "(none)"
        return "\n".join(f"- {name}: {type_name}" for name, type_name in parameters)

    def _fmt_local_lines() -> str:
        if not local_variables:
            return "(none)"
        return "\n".join(f"- {name}: {type_name}" for name, type_name in local_variables)

    return (
        "You are analyzing ONE Java method to build a DEBUG NAVIGATION MAP.\n"
        "\n"
        "Goal:\n"
        "Identify behavior-relevant logic and suppress ONLY observability noise.\n"
        "\n"
        "Hard constraints:\n"
        "1. Analyze ONLY the provided method.\n"
        "2. Do NOT assume behavior of unresolved methods.\n"
        "3. Do NOT remove logic-related calls (Optional, Collections, etc).\n"
        "4. Only classify logging/metrics as noise.\n"
        "5. Output MUST be valid JSON. No markdown. No explanations.\n"
        "\n"
        "---\n"
        "\n"
        "METHOD LOCATION:\n"
        f"file: {method.get('file_path', '')}\n"
        f"lines: {method.get('start_line', '')}-{method.get('end_line', '')}\n"
        "\n"
        "METHOD SIGNATURE:\n"
        f"{signature}\n"
        "\n"
        "CLASS:\n"
        f"{class_full_name}\n"
        f"Annotations: {class_annotations}\n"
        "\n"
        "---\n"
        "\n"
        "INPUT PARAMETERS:\n"
        f"{_fmt_param_lines()}\n"
        "# format:\n"
        "# - name: type\n"
        "\n"
        "---\n"
        "\n"
        "FIELDS USED:\n"
        "Reads:\n"
        f"{_fmt_lines(field_reads)}\n"
        "\n"
        "Writes:\n"
        f"{_fmt_lines(field_writes)}\n"
        "\n"
        "---\n"
        "\n"
        "LOCAL VARIABLES:\n"
        f"{_fmt_local_lines()}\n"
        "# format:\n"
        "# - name: type\n"
        "\n"
        "---\n"
        "\n"
        "CALLS FROM THIS METHOD:\n"
        f"{_fmt_lines(call_lines)}\n"
        "Each call includes:\n"
        "# - callee_name\n"
        "# - receiver (if any)\n"
        "# - receiver_type_normalized\n"
        "# - argument_count\n"
        "# - resolution_status\n"
        "# - resolution_reason\n"
        "\n"
        "---\n"
        "\n"
        "CLASSIFY CALLS USING THESE RULES:\n"
        "\n"
        "### Noise calls (ONLY these):\n"
        "- log.*\n"
        "- Markers.*\n"
        "- metrics.*\n"
        "- StatsDClient.*\n"
        "- Counter.*\n"
        "\n"
        "### NOT noise (these ARE logic):\n"
        "- Optional.*\n"
        "- Collections.*\n"
        "- Set.of / List.of / Map.of\n"
        "- Mono.*\n"
        "- Objects.*\n"
        "- StringUtils.*\n"
        "- CollectionUtils.*\n"
        "\n"
        "Treat these as:\n"
        "- data transformations\n"
        "- business conditions\n"
        "- control flow\n"
        "\n"
        "---\n"
        "\n"
        "METHOD SOURCE:\n"
        f"{method.get('source', '')}\n"
        "\n"
        "---\n"
        "\n"
        "Return JSON:\n"
        "\n"
        "{\n"
        '  "method_id": "",\n'
        '  "class_name": "",\n'
        '  "method_name": "",\n'
        '  "signature": "",\n'
        "\n"
        '  "inputs_used": [],\n'
        '  "fields_used": [],\n'
        "\n"
        '  "constants_used": [\n'
        "    {\n"
        '      "name": "",\n'
        '      "owner": "",\n'
        '      "usage": ""\n'
        "    }\n"
        "  ],\n"
        "\n"
        '  "objects_created_or_built": [\n'
        "    {\n"
        '      "variable": "",\n'
        '      "type": "",\n'
        '      "creation": "",\n'
        '      "reason": ""\n'
        "    }\n"
        "  ],\n"
        "\n"
        '  "logical_calls": [\n'
        "    {\n"
        '      "callee_name": "",\n'
        '      "receiver_type": "",\n'
        '      "resolution_status": "",\n'
        '      "reason": ""\n'
        "    }\n"
        "  ],\n"
        "\n"
        '  "noise_calls": [\n'
        "    {\n"
        '      "callee_name": "",\n'
        '      "reason": ""\n'
        "    }\n"
        "  ],\n"
        "\n"
        '  "business_branches": [\n'
        "    {\n"
        '      "condition": "",\n'
        '      "uses": []\n'
        "    }\n"
        "  ],\n"
        "\n"
        '  "data_transformations": [\n'
        "    {\n"
        '      "target": "",\n'
        '      "expression": "",\n'
        '      "type": "defaulting|mapping|filtering|building"\n'
        "    }\n"
        "  ],\n"
        "\n"
        '  "state_changes": [],\n'
        "\n"
        '  "side_effects": [],\n'
        "\n"
        '  "possible_failure_points": [\n'
        "    {\n"
        '      "type": "ambiguous_call|unresolved_call|null_risk|external_dependency",\n'
        '      "detail": ""\n'
        "    }\n"
        "  ],\n"
        "\n"
        '  "next_debug_locations": [\n'
        "    {\n"
        '      "class_name": "",\n'
        '      "method_name": "",\n'
        '      "reason": "",\n'
        '      "priority": "high|medium|low"\n'
        "    }\n"
        "  ],\n"
        "\n"
        '  "unknowns": [\n'
        '    "string"\n'
        "  ]\n"
        "}\n"
        "\n"
    )
