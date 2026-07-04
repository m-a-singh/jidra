-- Static parsing gap queries for graph.db
-- Run: sqlite3 <path/to/graph.db> < static_parsing_gaps.sql
-- Or individually in any SQLite client

-- Q1: Resolution status distribution (run this first — shows scope of problem)
SELECT
    resolution_status,
    resolution_reason,
    COUNT(*) AS cnt
FROM callsites
WHERE variant = 'validated'
GROUP BY resolution_status, resolution_reason
ORDER BY cnt DESC;

-- Q2: Methods with most unresolved callsites (hotspots)
SELECT
    m.class_full_name,
    m.method_name,
    COUNT(*) AS unresolved_calls,
    GROUP_CONCAT(DISTINCT cs.resolution_reason) AS reasons
FROM callsites cs
JOIN methods m ON m.id = cs.caller_method_id
    AND m.variant = cs.variant
WHERE cs.variant = 'validated'
  AND cs.resolution_status != 'resolved'
GROUP BY m.class_full_name, m.method_name
ORDER BY unresolved_calls DESC
LIMIT 50;

-- Q3: Callee names that static analysis can't trace (reflection, lambdas, external libs)
SELECT
    cs.callee_name,
    cs.receiver_type_raw,
    cs.resolution_reason,
    COUNT(*) AS cnt
FROM callsites cs
WHERE cs.variant = 'validated'
  AND cs.resolution_status != 'resolved'
GROUP BY cs.callee_name, cs.resolution_reason
ORDER BY cnt DESC
LIMIT 50;

-- Q4: Methods with no source (indexed node but source not extracted)
SELECT
    class_full_name,
    method_name,
    file_path,
    start_line
FROM methods
WHERE variant = 'validated'
  AND (source IS NULL OR source = '')
ORDER BY class_full_name;

-- Q5: Inheritance edges pointing to classes not in the graph (external deps / generated stubs)
SELECT
    ie.source_class,
    ie.target_class,
    ie.relation,
    COUNT(*) OVER (PARTITION BY ie.target_class) AS target_ref_count
FROM inheritance_edges ie
WHERE ie.variant = 'validated'
  AND NOT EXISTS (
      SELECT 1 FROM classes c
      WHERE c.variant = 'validated'
        AND c.full_name = ie.target_class
  )
ORDER BY target_ref_count DESC, ie.source_class;

-- Q6: Short-name collisions (T8 root cause — multiple classes share same simple name)
SELECT
    SUBSTR(class_full_name, INSTR(class_full_name, '.') + 1) AS short_name,
    COUNT(DISTINCT class_full_name) AS collision_count,
    GROUP_CONCAT(DISTINCT class_full_name) AS full_names
FROM methods
WHERE variant = 'validated'
GROUP BY short_name
HAVING collision_count > 1
ORDER BY collision_count DESC
LIMIT 30;
