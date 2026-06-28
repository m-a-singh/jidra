"""
Pyright validation for Python code analysis.

Uses Pyright for:
- Detecting type errors and import issues
- Validating call graph accuracy
- Performance and quality metrics
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ValidationMetrics:
    """Metrics for code validation quality."""
    files_analyzed: int = 0
    error_count: int = 0
    warning_count: int = 0
    execution_time_sec: float = 0.0
    runs: int = 0
    failures: int = 0
    unresolved_imports: list[str] = field(default_factory=list)

    def success_rate(self) -> float:
        """Percentage of files with no errors."""
        if self.files_analyzed == 0:
            return 0.0
        return ((self.files_analyzed - self.error_count) / self.files_analyzed) * 100


class PyrightValidator:
    """
    Enterprise-grade code validation using Pyright.

    Focuses on:
    - Detecting unresolved imports (helps call resolution)
    - Finding type errors (improves accuracy)
    - Performance metrics
    - Graceful fallback on unavailability
    """

    def __init__(
        self,
        codebase_root: Path,
        timeout: int = 120,
    ):
        self.codebase_root = Path(codebase_root).resolve()
        self.timeout = timeout
        self.metrics = ValidationMetrics()
        self._last_diagnostics: list[dict[str, Any]] = []

    def validate(self) -> ValidationMetrics:
        """
        Validate codebase with Pyright.

        Returns:
            Validation metrics including errors and warnings.
        """
        self.metrics.runs += 1

        try:
            logger.info(f"Running Pyright validation on {self.codebase_root}")
            result = subprocess.run(
                ["pyright", str(self.codebase_root), "--outputjson"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )

            if result.returncode not in (0, 1):  # 0=ok, 1=found issues
                logger.warning(f"Pyright exit code {result.returncode}")
                self.metrics.failures += 1
                return self.metrics

            try:
                data = json.loads(result.stdout)
                self._extract_metrics(data)
                logger.info(
                    f"Validation complete: {self.metrics.error_count} errors, "
                    f"{self.metrics.warning_count} warnings across "
                    f"{self.metrics.files_analyzed} files"
                )
                return self.metrics
            except json.JSONDecodeError as e:
                logger.error(f"Pyright output invalid JSON: {e}")
                self.metrics.failures += 1
                return self.metrics

        except subprocess.TimeoutExpired:
            logger.warning(f"Pyright timeout after {self.timeout}s")
            self.metrics.failures += 1
            return self.metrics
        except FileNotFoundError:
            logger.warning("Pyright not found (optional). Install with: pip install pyright")
            self.metrics.failures += 1
            return self.metrics
        except Exception as e:
            logger.warning(f"Pyright validation unavailable: {e}")
            self.metrics.failures += 1
            return self.metrics

    def _extract_metrics(self, data: dict[str, Any]) -> None:
        """Extract validation metrics from Pyright output."""
        summary = data.get("summary", {})
        self.metrics.files_analyzed = summary.get("filesAnalyzed", 0)
        self.metrics.error_count = summary.get("errorCount", 0)
        self.metrics.warning_count = summary.get("warningCount", 0)
        self.metrics.execution_time_sec = float(summary.get("timeInSec", 0))

        diagnostics = data.get("generalDiagnostics", [])
        self._last_diagnostics = diagnostics

        for diag in diagnostics:
            if "could not be resolved" in diag.get("message", "").lower():
                self.metrics.unresolved_imports.append(
                    f"{diag.get('file', 'unknown')}: {diag.get('message', '')}"
                )

    def get_type_hints(self) -> dict[tuple[str, int], str]:
        """
        Extract inferred receiver types from Pyright diagnostics.

        Parses messages like 'Cannot access attribute "foo" for class "Bar"'
        to produce a {(abs_file_path, line): class_name} map. Used by the
        call-resolution pre-pass to enrich call sites that the symbol table
        could not type (receiver_type=None), converting Phase-2/3/4 guesses
        into Phase-1 exact matches.

        Returns {} when Pyright was unavailable or produced no diagnostics.
        """
        hints: dict[tuple[str, int], str] = {}
        # Patterns Pyright emits that reveal the receiver type at a call site:
        #   'Cannot access attribute "x" for class "Foo"'
        #   'Cannot access member "x" for type "Foo"'
        _pattern = re.compile(r'(?:for class|for type) "([A-Za-z_][A-Za-z0-9_]*)"')
        for diag in self._last_diagnostics:
            m = _pattern.search(diag.get("message", ""))
            if not m:
                continue
            file_path = diag.get("file", "")
            line = diag.get("range", {}).get("start", {}).get("line", -1)
            if file_path and line >= 0:
                hints[(file_path, line)] = m.group(1)
        return hints

    def get_metrics(self) -> ValidationMetrics:
        """Return collected validation metrics."""
        return self.metrics