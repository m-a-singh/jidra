import json
import sqlite3
import sys
from collections import Counter, defaultdict


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_patterns.py /path/to/jidra.db")
        sys.exit(1)

    db_path = sys.argv[1]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # --- Graph stats ---
    class_count = conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
    method_count = conn.execute("SELECT COUNT(*) FROM methods").fetchone()[0]
    callsite_count = conn.execute("SELECT COUNT(*) FROM callsites").fetchone()[0]
    inherit_count = conn.execute("SELECT COUNT(*) FROM inheritance_edges").fetchone()[0]
    field_count = conn.execute("SELECT COUNT(*) FROM fields").fetchone()[0]

    print("Graph stats:")
    print(f"  Classes:     {class_count}")
    print(f"  Methods:     {method_count}")
    print(f"  Callsites:   {callsite_count}")
    print(f"  Inheritance: {inherit_count}")
    print(f"  Fields:      {field_count}")

    # --- Resolution status distribution ---
    status_rows = conn.execute("""
        SELECT resolution_status, COUNT(*) as cnt
        FROM callsites
        GROUP BY resolution_status
        ORDER BY cnt DESC
    """).fetchall()

    total = sum(r["cnt"] for r in status_rows)
    resolved = sum(
        r["cnt"]
        for r in status_rows
        if r["resolution_status"] and r["resolution_status"].startswith("resolved")
    )

    print("\nResolution overview:")
    print(f"  Total callsites: {total}")
    print(f"  Resolved:        {resolved} ({100 * resolved / total:.1f}%)")
    print(f"  Unresolved/Ambiguous: {total - resolved} ({100 * (total - resolved) / total:.1f}%)")

    print("\nStatus breakdown:")
    for r in status_rows:
        print(f"  {r['resolution_status']}: {r['cnt']} ({100 * r['cnt'] / total:.1f}%)")

    # --- Load unresolved callsites with context ---
    unresolved_rows = conn.execute("""
        SELECT
            cs.id, cs.caller_method_id, cs.callee_name, cs.receiver,
            cs.receiver_type_raw, cs.receiver_type_normalized,
            cs.resolution_status, cs.resolution_reason, cs.candidate_count,
            m.class_id AS caller_class_id,
            m.class_full_name AS caller_class_name,
            m.method_name AS caller_method_name,
            c.annotations_json AS caller_class_annotations,
            c.stereotypes_json AS caller_class_stereotypes,
            c.implements_json AS caller_class_implements
        FROM callsites cs
        JOIN methods m ON cs.caller_method_id = m.id
            AND cs.variant = m.variant AND cs.module_id = m.module_id
        JOIN classes c ON m.class_id = c.id
            AND m.variant = c.variant AND m.module_id = c.module_id
        WHERE cs.resolution_status NOT LIKE 'resolved%'
           OR cs.resolution_status LIKE 'ambiguous%'
    """).fetchall()

    if not unresolved_rows:
        print("\nNo unresolved callsites! Nothing for a model to fix.")
        conn.close()
        return

    # --- Build lookups ---
    # implementers: interface/parent -> [implementing classes]
    implementers = defaultdict(list)
    for row in conn.execute("SELECT source_class, target_class, relation FROM inheritance_edges"):
        if row["relation"] == "implements":
            implementers[row["target_class"]].append(row["source_class"])

    # all known class names
    all_classes = set()
    for row in conn.execute("SELECT full_name, name FROM classes"):
        all_classes.add(row["full_name"])
        all_classes.add(row["name"])

    # fields by class_id
    fields_by_class = defaultdict(list)
    for row in conn.execute("SELECT class_id, name, type_name FROM fields"):
        fields_by_class[row["class_id"]].append(row)

    # --- Categorize into pattern buckets ---
    pattern_buckets = Counter()
    examples = defaultdict(list)

    for cs in unresolved_rows:
        status = cs["resolution_status"] or "unknown"
        receiver_raw = cs["receiver_type_raw"] or cs["receiver"] or ""
        callee = cs["callee_name"] or ""
        stereotypes = json.loads(cs["caller_class_stereotypes"] or "[]")
        _annotations = json.loads(cs["caller_class_annotations"] or "[]")

        pattern = "unknown"

        # 1. Spring DI: field on caller class matches the receiver
        caller_fields = fields_by_class.get(cs["caller_class_id"], [])
        matching_field = None
        for f in caller_fields:
            if f["name"] == cs["receiver"] or f["type_name"] == receiver_raw:
                matching_field = f
                break

        if matching_field:
            field_type = matching_field["type_name"]
            impls = implementers.get(field_type, [])
            if len(impls) == 1:
                pattern = "SPRING_DI_SINGLE_IMPL"
            elif len(impls) > 1:
                pattern = "SPRING_DI_MULTI_IMPL"
            elif field_type in all_classes:
                pattern = "FIELD_TYPE_KNOWN_NO_IMPL_EDGE"
            else:
                pattern = "FIELD_TYPE_EXTERNAL"

        # 2. Fluent/builder chain
        elif receiver_raw and receiver_raw.rstrip().endswith(")"):
            pattern = "FLUENT_CHAIN"

        # 3. Static call on a known class
        elif receiver_raw and receiver_raw[0:1].isupper() and "." not in receiver_raw:
            if receiver_raw in all_classes:
                pattern = "STATIC_CALL_CLASS_EXISTS"
            else:
                pattern = "STATIC_CALL_CLASS_MISSING"

        # 4. External library
        elif status == "external_library":
            pattern = "EXTERNAL_LIBRARY"

        # 5. Ambiguous overload
        elif status == "ambiguous_overload":
            pattern = "AMBIGUOUS_OVERLOAD"

        # 6. Ambiguous type
        elif status == "ambiguous_type":
            pattern = "AMBIGUOUS_TYPE"

        # 7. this.something chain
        elif receiver_raw and receiver_raw.startswith("this."):
            pattern = "THIS_CHAIN_UNRESOLVED"

        else:
            pattern = f"OTHER_{status}"

        pattern_buckets[pattern] += 1
        if len(examples[pattern]) < 3:
            examples[pattern].append(
                {
                    "caller": f"{cs['caller_class_name']}.{cs['caller_method_name']}",
                    "callee": callee,
                    "receiver": receiver_raw,
                    "status": status,
                    "reason": cs["resolution_reason"],
                    "stereotypes": stereotypes,
                }
            )

    # --- Report ---
    LEARNABLE = {
        "SPRING_DI_SINGLE_IMPL",
        "SPRING_DI_MULTI_IMPL",
        "FIELD_TYPE_KNOWN_NO_IMPL_EDGE",
        "AMBIGUOUS_OVERLOAD",
        "AMBIGUOUS_TYPE",
        "STATIC_CALL_CLASS_EXISTS",
        "THIS_CHAIN_UNRESOLVED",
    }

    print(f"\n{'=' * 60}")
    print(f"PATTERN ANALYSIS OF {len(unresolved_rows)} UNRESOLVED/AMBIGUOUS CALLSITES")
    print(f"{'=' * 60}")

    for pattern, count in pattern_buckets.most_common():
        pct = 100 * count / len(unresolved_rows)
        tag = "LEARNABLE" if pattern in LEARNABLE else "HARD/EXTERNAL"

        print(f"\n  [{tag}] {pattern}: {count} ({pct:.1f}%)")
        for ex in examples[pattern]:
            print(f"    Ex: {ex['caller']} -> .{ex['callee']}()")
            print(f"        receiver={ex['receiver']}, status={ex['status']}")
            if ex["stereotypes"]:
                print(f"        stereotypes={ex['stereotypes']}")

    learnable_count = sum(v for k, v in pattern_buckets.items() if k in LEARNABLE)

    print(f"\n{'=' * 60}")
    print("VERDICT")
    print(f"{'=' * 60}")
    print(
        f"  Learnable: {learnable_count}/{len(unresolved_rows)} ({100 * learnable_count / len(unresolved_rows):.1f}%)"
    )
    print(f"  External/hard: {len(unresolved_rows) - learnable_count}/{len(unresolved_rows)}")
    print()
    if learnable_count / len(unresolved_rows) > 0.3:
        print("  -> YES: MiniLM can meaningfully improve your graph.")
        print("     Focus on the top LEARNABLE buckets first.")
    else:
        print("  -> MAYBE: Most gaps are external/hard cases.")
        print("     A model helps less — consider enriching the extractor instead.")

    conn.close()


if __name__ == "__main__":
    main()
