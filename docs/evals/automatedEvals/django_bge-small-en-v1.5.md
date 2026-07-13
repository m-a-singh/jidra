# Retrieval Eval — django/django

**Date:** 2026-07-12  
**Cases:** 114  
**Inputs:**
- JIDRA search: `evals/dataset/results/results_jidra_search.json`
- JIDRA explore: `evals/dataset/results/results_jidra_explore.json`
- CG search: `evals/dataset/results/results_cg_search.json`
- CG explore: `evals/dataset/results/results_cg_explore.json`

---

## Summary

| Metric | JIDRA search | JIDRA explore | CG search | CG explore† |
|--------|-------------|--------------|-----------|-------------|

> † CG explore = 1-hop edge expansion approximation — not CodeGraph's native semantic explore. Underestimates real CG explore performance.

| Pass rate | 0.9386 | 0.8772 | 0.3070 | 0.1579 |
| Mean recall | 0.9386 | 0.8772 | 0.3070 | 0.1579 |
| Mean MRR | 0.5910 | 0.5890 | 0.1056 | 0.0924 |
| Latency mean | 893ms | 294ms | 2ms | 1ms |
| Latency p50 | 807ms | 230ms | 1ms | 0ms |
| Latency p95 | 1558ms | 475ms | 8ms | 4ms |
| Pass count | 107/114 | 100/114 | 35/114 | 18/114 |

### JIDRA vs CG — best mode delta

| Metric | Best JIDRA | Best CG | Δ (JIDRA − CG) |
|--------|-----------|---------|----------------|
| Pass rate | 0.9386 | 0.3070 | +0.6316 |
| Mean recall | 0.9386 | 0.3070 | +0.6316 |
| Mean MRR | 0.5910 | 0.1056 | +0.4854 |

### Visual

```
Pass rate
  JIDRA search   ███████████████░  93.9%
  JIDRA explore  ██████████████░░  87.7%
  CG search      █████░░░░░░░░░░░  30.7%
  CG explore     ███░░░░░░░░░░░░░  15.8%

Mean recall
  JIDRA search   ███████████████░  93.9%
  JIDRA explore  ██████████████░░  87.7%
  CG search      █████░░░░░░░░░░░  30.7%
  CG explore     ███░░░░░░░░░░░░░  15.8%

Latency (mean ms — lower is better)
  JIDRA search     ████████████████████  893ms
  JIDRA explore    ███████░░░░░░░░░░░░░  294ms
  CG search        ░░░░░░░░░░░░░░░░░░░░  2ms
  CG explore†      ░░░░░░░░░░░░░░░░░░░░  1ms
```

---

## System Win / Loss

> Pass = either search **or** explore passes for that system.

| Outcome | Count |
|---------|-------|
| Both pass | 35 |
| JIDRA only | 72 |
| CG only | 1 |
| Both fail | 6 |
| **Total** | **114** |

**JIDRA net: +71 cases** (72 exclusive wins vs 1)

---

## JIDRA Exclusive Wins (72 cases)

JIDRA (search or explore) passes; CG (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File |
|------|-------------|--------------|-----------|------------|------|
| django__django-11001 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/sql/compiler.py` |
| django__django-11039 | 1.00 | 1.00 | 0.00 | 0.00 | `django/core/management/commands/sqlmigrate.py` |
| django__django-11133 | 1.00 | 1.00 | 0.00 | 0.00 | `django/http/response.py` |
| django__django-11179 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/deletion.py` |
| django__django-11283 | 1.00 | 1.00 | 0.00 | 0.00 | `django/contrib/auth/migrations/0011_update_proxy_permissions.py` |
| django__django-11564 | 1.00 | 1.00 | 0.00 | 0.00 | `django/conf/__init__.py` |
| django__django-11620 | 1.00 | 1.00 | 0.00 | 0.00 | `django/views/debug.py` |
| django__django-11630 | 1.00 | 1.00 | 0.00 | 0.00 | `django/core/checks/model_checks.py` |
| django__django-11848 | 1.00 | 1.00 | 0.00 | 0.00 | `django/utils/http.py` |
| django__django-11910 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/migrations/autodetector.py` |
| django__django-11964 | 1.00 | 0.00 | 0.00 | 0.00 | `django/db/models/enums.py` |
| django__django-12113 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/backends/sqlite3/creation.py` |
| django__django-12125 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/migrations/serializer.py` |
| django__django-12286 | 1.00 | 1.00 | 0.00 | 0.00 | `django/core/checks/translation.py` |
| django__django-12470 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/sql/compiler.py` |
| django__django-12497 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/fields/related.py` |
| django__django-12589 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/sql/query.py` |
| django__django-12700 | 1.00 | 1.00 | 0.00 | 0.00 | `django/views/debug.py` |
| django__django-12708 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/backends/base/schema.py` |
| django__django-12747 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/deletion.py` |
| django__django-12856 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/base.py` |
| django__django-12915 | 1.00 | 1.00 | 0.00 | 0.00 | `django/contrib/staticfiles/handlers.py` |
| django__django-12983 | 1.00 | 1.00 | 0.00 | 0.00 | `django/utils/text.py` |
| django__django-13033 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/sql/compiler.py` |
| django__django-13158 | 1.00 | 0.00 | 0.00 | 0.00 | `django/db/models/sql/query.py` |
| django__django-13230 | 1.00 | 1.00 | 0.00 | 0.00 | `django/contrib/syndication/views.py` |
| django__django-13265 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/migrations/autodetector.py` |
| django__django-13401 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/fields/__init__.py` |
| django__django-13447 | 1.00 | 1.00 | 0.00 | 0.00 | `django/contrib/admin/sites.py` |
| django__django-13448 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/backends/base/creation.py` |
| django__django-13551 | 1.00 | 1.00 | 0.00 | 0.00 | `django/contrib/auth/tokens.py` |
| django__django-13660 | 1.00 | 1.00 | 0.00 | 0.00 | `django/core/management/commands/shell.py` |
| django__django-13757 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/fields/json.py` |
| django__django-13768 | 1.00 | 1.00 | 0.00 | 0.00 | `django/dispatch/dispatcher.py` |
| django__django-13925 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/base.py` |
| django__django-13933 | 1.00 | 1.00 | 0.00 | 0.00 | `django/forms/models.py` |
| django__django-14016 | 1.00 | 0.00 | 0.00 | 0.00 | `django/db/models/query_utils.py` |
| django__django-14017 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/query_utils.py` |
| django__django-14155 | 1.00 | 1.00 | 0.00 | 0.00 | `django/urls/resolvers.py` |
| django__django-14238 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/fields/__init__.py` |
| django__django-14382 | 1.00 | 1.00 | 0.00 | 0.00 | `django/core/management/templates.py` |
| django__django-14534 | 1.00 | 1.00 | 0.00 | 0.00 | `django/forms/boundfield.py` |
| django__django-14580 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/migrations/serializer.py` |
| django__django-14608 | 1.00 | 1.00 | 0.00 | 0.00 | `django/forms/formsets.py` |
| django__django-14667 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/sql/query.py` |
| django__django-14672 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/fields/reverse_related.py` |
| django__django-14730 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/fields/related.py` |
| django__django-14787 | 1.00 | 1.00 | 0.00 | 0.00 | `django/utils/decorators.py` |
| django__django-14915 | 1.00 | 1.00 | 0.00 | 0.00 | `django/forms/models.py` |
| django__django-15061 | 1.00 | 1.00 | 0.00 | 0.00 | `django/forms/widgets.py` |
| django__django-15252 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/migrations/executor.py` |
| django__django-15320 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/expressions.py` |
| django__django-15388 | 1.00 | 0.00 | 0.00 | 0.00 | `django/template/autoreload.py` |
| django__django-15498 | 1.00 | 1.00 | 0.00 | 0.00 | `django/views/static.py` |
| django__django-15738 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/migrations/autodetector.py` |
| django__django-15789 | 1.00 | 1.00 | 0.00 | 0.00 | `django/utils/html.py` |
| django__django-15790 | 1.00 | 1.00 | 0.00 | 0.00 | `django/core/checks/templates.py` |
| django__django-15814 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/sql/query.py` |
| django__django-15851 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/backends/postgresql/client.py` |
| django__django-15902 | 1.00 | 1.00 | 0.00 | 0.00 | `django/forms/formsets.py` |
| django__django-15996 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/migrations/serializer.py` |
| django__django-16046 | 1.00 | 1.00 | 0.00 | 0.00 | `django/utils/numberformat.py` |
| django__django-16139 | 1.00 | 1.00 | 0.00 | 0.00 | `django/contrib/auth/forms.py` |
| django__django-16229 | 1.00 | 1.00 | 0.00 | 0.00 | `django/forms/boundfield.py` |
| django__django-16255 | 1.00 | 1.00 | 0.00 | 0.00 | `django/contrib/sitemaps/__init__.py` |
| django__django-16400 | 1.00 | 0.00 | 0.00 | 0.00 | `django/contrib/auth/management/__init__.py` |
| django__django-16527 | 1.00 | 1.00 | 0.00 | 0.00 | `django/contrib/admin/templatetags/admin_modify.py` |
| django__django-16595 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/migrations/operations/fields.py` |
| django__django-16816 | 1.00 | 1.00 | 0.00 | 0.00 | `django/contrib/admin/checks.py` |
| django__django-16873 | 1.00 | 1.00 | 0.00 | 0.00 | `django/template/defaultfilters.py` |
| django__django-16910 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/sql/query.py` |
| django__django-17051 | 1.00 | 1.00 | 0.00 | 0.00 | `django/db/models/query.py` |

---

## CG Exclusive Wins (1 cases)

CG (search or explore) passes; JIDRA (search and explore) both fail.

| Case | JIDRA search | JIDRA explore | CG search | CG explore | File | Gap |
|------|-------------|--------------|-----------|------------|------|-----|
| django__django-14997 | 0.00 | 0.00 | 1.00 | 0.00 | `django/db/backends/ddl_references.py` | FTS term mismatch |

---

## Both Fail (6 cases)

Neither system finds the expected file with search or explore.

| Case | Expected file |
|------|---------------|
| django__django-10914 | `django/conf/global_settings.py` |
| django__django-11797 | `django/db/models/lookups.py` |
| django__django-15213 | `django/db/models/fields/__init__.py` |
| django__django-15819 | `django/core/management/commands/inspectdb.py` |
| django__django-16408 | `django/db/models/sql/compiler.py` |
| django__django-17087 | `django/db/migrations/serializer.py` |

---

## Key Insights

1. **Best JIDRA mode** (93.9%) vs **best CG mode** (30.7%) — JIDRA advantage: +0.6316.
2. **JIDRA explore** (87.7%) is the strongest single mode; **CG search** (30.7%) is CG's strongest.
3. **Latency** — JIDRA best: 294ms mean, CG best: 1ms mean (0% of JIDRA).
4. **CG explore** (15.8%) is weak — 1-hop edge expansion approximation, not CG's real semantic explore.
5. **Both fail 6 cases** — config files, enums, migration files with sparse method content.
