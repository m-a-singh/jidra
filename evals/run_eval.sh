#!/usr/bin/env bash
JIDRA_BIN="${JIDRA_BIN:-/Users/akhil/Personal_Project/jidra/.venv/bin/jidra}"
# Usage:
#   ./evals/run_eval.sh <repo_root> <jidra_repo_arg> <cases_file>
#
# Example:
#   ./evals/run_eval.sh \
#     /Users/akhil/Personal_Project/jidra_analysis_repos/scikit-learn \
#     scikit-learn/scikit-learn \
#     evals/dataset/ts_python_test_cases.yaml
#
# Runs all 10 models in sequence. Pre-eval check must pass before any model runs.
# Results written to:
#   evals/dataset/results/compare_four_<repo>_<model>.json
#   docs/evals/automatedEvals/<repo>_<model>.md

set -euo pipefail

REPO_ROOT="${1:?Usage: $0 <repo_root> <jidra_repo_arg> <cases_file>}"
JIDRA_REPO_ARG="${2:?}"
CASES_FILE="${3:?}"

JIDRA_DB="${REPO_ROOT}/.jidra/graph.db"
CG_DB="${REPO_ROOT}/.codegraph/codegraph.db"
WORK_DIR="evals/dataset/results"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Derive short names for output files
REPO_SHORT="${JIDRA_REPO_ARG##*/}"

MODELS=(
    "nomic-ai/nomic-embed-text-v2-moe"
    "sentence-transformers/all-mpnet-base-v2"
    "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
    "sentence-transformers/multi-qa-distilbert-cos-v1"
    "huggingface/CodeBERTa-small-v1"
    "microsoft/graphcodebert-base"
    "sentence-transformers/all-DistilROBERTa-v1"
    "sentence-transformers/multi-qa-distilbert-dot-v1"
    "microsoft/unixcoder-base"
    "BAAI/bge-small-en-v1.5"
)

cd "${PROJECT_ROOT}"

mkdir -p "${WORK_DIR}"
mkdir -p "docs/evals/automatedEvals"

echo ""
echo "=== Pre-eval check ==="
if ! python3 evals/pre_eval_check.py "${JIDRA_DB}"; then
    echo ""
    echo "Pre-eval check FAILED. Fix issues above before running evals."
    exit 1
fi

echo ""
echo "=== Starting eval run ==="
echo "  repo:     ${JIDRA_REPO_ARG}"
echo "  jidra_db: ${JIDRA_DB}"
echo "  cg_db:    ${CG_DB}"
echo "  cases:    ${CASES_FILE}"
echo "  models:   ${#MODELS[@]}"
echo ""

TOTAL=${#MODELS[@]}
DONE=0
FAILED=()

for MODEL in "${MODELS[@]}"; do
    MODEL_SHORT="${MODEL##*/}"
    OUT_JSON="${WORK_DIR}/compare_four_${REPO_SHORT}_${MODEL_SHORT}.json"
    OUT_MD="docs/evals/automatedEvals/${REPO_SHORT}_${MODEL_SHORT}.md"

    echo "--- [${DONE}/${TOTAL}] model: ${MODEL} ---"

    # Embed
    echo "  embed-index..."
    if ! "${JIDRA_BIN}" embed-index --graph "${JIDRA_DB}" --model "${MODEL}"; then
        echo "  FAILED: embed-index for ${MODEL}"
        FAILED+=("${MODEL}")
        sqlite3 "${JIDRA_DB}" "DELETE FROM method_embeddings" 2>/dev/null || true
        DONE=$((DONE + 1))
        continue
    fi

    # Eval
    echo "  running eval..."
    if ! python3 evals/dataset/compare_four.py \
        --jidra-db "${JIDRA_DB}" \
        --cg-db    "${CG_DB}" \
        --cases    "${CASES_FILE}" \
        --repo     "${JIDRA_REPO_ARG}" \
        --work-dir "${WORK_DIR}" \
        --out      "${OUT_JSON}" \
        --report   "${OUT_MD}"; then
        echo "  FAILED: eval for ${MODEL}"
        FAILED+=("${MODEL}")
    else
        echo "  done: ${OUT_JSON}"
    fi

    # Clear embeddings
    echo "  clearing embeddings..."
    sqlite3 "${JIDRA_DB}" "DELETE FROM method_embeddings"

    DONE=$((DONE + 1))
    echo ""
done

echo "=== Run complete: ${DONE}/${TOTAL} models ==="
if [ ${#FAILED[@]} -gt 0 ]; then
    echo "FAILED models:"
    for m in "${FAILED[@]}"; do
        echo "  - ${m}"
    done
    exit 1
else
    echo "All models completed successfully."
fi
