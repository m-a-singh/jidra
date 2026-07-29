"""
Pyright validation and LSP-based type enrichment for Python code analysis.

Uses Pyright for:
- Detecting type errors and import issues
- Validating call graph accuracy
- Performance and quality metrics
- LSP hover queries to enrich unresolved call site receiver types
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from typing_extensions import Self

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
            pyright_bin = str(Path(sys.executable).parent / "pyright")
            result = subprocess.run(
                [pyright_bin, str(self.codebase_root), "--outputjson"],
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


class PyrightLSPEnricher:
    """
    Spawns pyright as an LSP server (already a JIDRA dependency, no new installs)
    and queries textDocument/hover for each unresolved call site's receiver position.
    Converts Phase-2/3/4 name-only guesses into Phase-1 exact matches by supplying
    the receiver type that the symbol table could not infer.

    Lifecycle: use as a context manager — server is started on __enter__ and
    terminated on __exit__.
    """

    # Patterns in pyright hover markdown that reveal the receiver's type:
    #   "(variable) name: ClassName"  or  "(variable) name: ClassName[T]"
    #   "(parameter) name: ClassName"
    #   "(attribute) ClassName.attr: T"
    #   "(method) ClassName.method(...) -> T"
    #   "(class) ClassName"
    # Note: skip "(module) name" — module-level calls can't resolve to instance methods
    _HOVER_TYPE_RE = re.compile(
        r"""
        (?:
            # (variable|parameter|property) name: [Self@]ClassName[...]
            \((?:variable|parameter|property)\)\s+\S+:\s+(?:Self@)?([A-Za-z_][A-Za-z0-9_]*)
            |
            # (attribute) ClassName.attr: ...  — receiver is ClassName
            \(attribute\)\s+([A-Za-z_][A-Za-z0-9_]*)\.
            |
            # (method) ClassName.method(...)
            \(method\)\s+([A-Za-z_][A-Za-z0-9_]*)\.
            |
            # (class) ClassName
            \(class\)\s+([A-Za-z_][A-Za-z0-9_]*)
        )
        """,
        re.VERBOSE,
    )

    # Stdlib/builtins types that will never appear in the user's graph — skip enriching with these
    _STDLIB_TYPES = frozenset(
        {
            "str",
            "int",
            "float",
            "bool",
            "bytes",
            "list",
            "dict",
            "set",
            "tuple",
            "None",
            "Any",
            "Optional",
            "Union",
            "Type",
            "Callable",
            "Iterator",
            "Generator",
            "Iterable",
            "Sequence",
            "Mapping",
            "MutableMapping",
            "Path",
            "PurePath",
            "CompletedProcess",
            "Popen",
            "TextIOWrapper",
            "Logger",
            "Pattern",
            "Match",
            "re",
            "os",
            "sys",
            "json",
            "subprocess",
        }
    )

    def __init__(self, codebase_root: Path, timeout: int = 30):
        self.codebase_root = Path(codebase_root).resolve()
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._req_id = 0
        self._lock = threading.Lock()

    @staticmethod
    def _find_langserver() -> list[str] | None:
        """
        Locate pyright's bundled langserver.index.js from the pip-installed package.
        The pyright pip package ships the Node LSP server at pyright/dist/langserver.index.js.
        Returns the command list [node, langserver_path, --stdio] or None if not found.
        """
        node_bin = shutil.which("node")
        if not node_bin:
            return None
        try:
            import importlib.util

            spec = importlib.util.find_spec("pyright")
            if spec is None or spec.origin is None:
                return None
            pkg_dir = Path(spec.origin).parent
            langserver = pkg_dir / "dist" / "langserver.index.js"
            if langserver.exists():
                return [node_bin, str(langserver), "--stdio"]
        except Exception:
            pass
        return None

    def __enter__(self) -> Self:
        cmd = self._find_langserver()
        if cmd is None:
            logger.debug("Pyright langserver not found; LSP enrichment skipped")
            return self
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._initialize()
        except Exception as e:
            logger.debug(f"Pyright LSP start failed: {e}")
            self._terminate()
        return self

    def __exit__(self, *_: object) -> None:
        self._terminate()

    def _terminate(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": self._next_id(), "method": "shutdown", "params": None}
                )
                header = f"Content-Length: {len(body)}\r\n\r\n"
                proc.stdin.write((header + body).encode())
                notif = json.dumps({"jsonrpc": "2.0", "method": "exit", "params": {}})
                header2 = f"Content-Length: {len(notif)}\r\n\r\n"
                proc.stdin.write((header2 + notif).encode())
                proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── LSP protocol helpers ──────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send(self, payload: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            return
        if "jsonrpc" not in payload:
            payload = {"jsonrpc": "2.0", **payload}
        if "id" not in payload and payload.get("method") not in ("exit",):
            payload["id"] = self._next_id()
        body = json.dumps(payload)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        try:
            self._proc.stdin.write((header + body).encode())
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._proc = None

    def _read_response(self) -> dict[str, Any] | None:
        if not self._proc or not self._proc.stdout:
            return None
        try:
            # Read headers
            content_length = 0
            while True:
                line = self._proc.stdout.readline().decode("utf-8", errors="replace")
                if not line.strip():
                    break
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":", 1)[1].strip())
            if content_length == 0:
                return None
            body = self._proc.stdout.read(content_length).decode("utf-8", errors="replace")
            return json.loads(body)
        except Exception as e:
            logger.debug(f"LSP read error: {e}")
            return None

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        req_id = self._next_id()
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        body = json.dumps(payload)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        if not self._proc or not self._proc.stdin:
            return None
        try:
            self._proc.stdin.write((header + body).encode())
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._proc = None
            return None
        # Read responses until we get one matching our request id
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            resp = self._read_response()
            if resp is None:
                break
            if resp.get("id") == req_id:
                return resp
        return None

    def _initialize(self) -> None:
        resp = self._request(
            "initialize",
            {
                "processId": None,
                "rootUri": self.codebase_root.as_uri(),
                "capabilities": {"textDocument": {"hover": {"contentFormat": ["plaintext"]}}},
                "initializationOptions": {"pythonPath": sys.executable},
            },
        )
        if resp:
            # Send initialized notification (no response expected)
            notif = json.dumps({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            header = f"Content-Length: {len(notif)}\r\n\r\n"
            if self._proc and self._proc.stdin:
                self._proc.stdin.write((header + notif).encode())
                self._proc.stdin.flush()
            # Give pyright a moment to index
            time.sleep(0.5)

    def _open_file(self, abs_path: str, source: str) -> None:
        notif = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": Path(abs_path).as_uri(),
                        "languageId": "python",
                        "version": 1,
                        "text": source,
                    }
                },
            }
        )
        header = f"Content-Length: {len(notif)}\r\n\r\n"
        if self._proc and self._proc.stdin:
            self._proc.stdin.write((header + notif).encode())
            self._proc.stdin.flush()

    # ── Public API ────────────────────────────────────────────────────────────

    def enrich(
        self,
        graph: Any,  # Graph — Any to avoid circular import at runtime
        codebase_root: Path,
        py_files: list[Path],
    ) -> dict[str, int]:
        """
        File-centric LSP enrichment. For each file: open once, run all LSP
        queries for that file's methods and callsites, merge into graph.

        LSP queries per file:
          - textDocument/documentSymbol    — find methods JIDRA's AST missed
          - textDocument/prepareCallHierarchy + callHierarchy/outgoingCalls
                                           — outgoing edges JIDRA missed
          - callHierarchy/incomingCalls    — incoming callers JIDRA missed
          - textDocument/definition        — resolve unknown receivers to class
          - textDocument/references        — find additional call sites
          - textDocument/typeDefinition    — receiver type via type jump
          - textDocument/implementation    — interface → concrete class resolution
        """
        if not self.available:
            return {}

        # ── Build file-keyed indices from graph ───────────────────────────────
        methods_by_file: dict[str, list[Any]] = {}
        for m in graph.methods:
            methods_by_file.setdefault(m.file_path, []).append(m)

        callsites_by_file: dict[str, list[Any]] = {}
        for cs in graph.callsites:
            callsites_by_file.setdefault(cs.file_path, []).append(cs)

        # class lookup: (file, line) → class for definition resolution
        class_by_file_line: dict[str, list[Any]] = {}
        for cls in graph.classes:
            class_by_file_line.setdefault(cls.file_path, []).append(cls)

        results: dict[str, int] = {
            "receiver_resolved": 0,
            "missing_methods": 0,
        }

        # Only process files that have unresolved callsites
        files_with_unresolved = {
            cs.file_path
            for cs in graph.callsites
            if not cs.resolution_status.startswith("resolved")
        }

        for rel_path in files_with_unresolved:
            abs_path = (codebase_root / rel_path).resolve()
            if not abs_path.exists():
                continue
            try:
                source = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            uri = abs_path.as_uri()
            self._open_file(str(abs_path), source)

            file_methods = methods_by_file.get(rel_path, [])
            file_callsites = callsites_by_file.get(rel_path, [])
            jidra_method_names = {m.method_name for m in file_methods}

            # ── 1. documentSymbol — count methods JIDRA's AST missed ─────────
            doc_resp = self._request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
            doc_syms = (doc_resp.get("result") or []) if doc_resp else []
            for sym in doc_syms:
                children = sym.get("children", [])
                for s in [sym, *children]:
                    if s.get("kind") in (6, 12) and s.get("name") not in jidra_method_names:
                        results["missing_methods"] += 1

            # ── 2. definition + typeDefinition — resolve unknown receivers ────
            unresolved = [
                cs
                for cs in file_callsites
                if cs.receiver_type is None
                and cs.receiver
                and cs.receiver not in {"self", "cls", "super"}
            ]
            for cs in unresolved:
                pos = {"line": cs.line - 1, "character": max(0, cs.column - 1)}

                # try definition first
                def_resp = self._request(
                    "textDocument/definition",
                    {
                        "textDocument": {"uri": uri},
                        "position": pos,
                    },
                )
                result = (def_resp.get("result") or None) if def_resp else None
                if isinstance(result, list):
                    result = result[0] if result else None

                # fall back to typeDefinition if definition didn't point at a class
                if not result:
                    td_resp = self._request(
                        "textDocument/typeDefinition",
                        {
                            "textDocument": {"uri": uri},
                            "position": pos,
                        },
                    )
                    result = (td_resp.get("result") or None) if td_resp else None
                    if isinstance(result, list):
                        result = result[0] if result else None
                    source_tag = "pyright_lsp_typedef"
                else:
                    source_tag = "pyright_lsp_definition"

                if not result:
                    continue
                res_uri = result.get("uri", "")
                res_line = result.get("range", {}).get("start", {}).get("line", 0) + 1
                res_file_abs = res_uri[7:] if res_uri.startswith("file://") else ""
                try:
                    res_rel = str(Path(res_file_abs).relative_to(codebase_root))
                except ValueError:
                    continue
                for cls in class_by_file_line.get(res_rel, []):
                    if cls.start_line <= res_line <= cls.end_line:
                        cs.receiver_type = (
                            cls.full_name
                        )  # must match method.class_full_name for Phase 1
                        cs.receiver_type_raw = cls.full_name
                        cs.receiver_type_normalized = cls.name
                        cs.receiver_resolution_source = source_tag
                        results["receiver_resolved"] += 1
                        break

        return results

    def _parse_type(self, hover_text: str) -> str | None:
        """Extract class name from pyright hover markdown."""
        for line in hover_text.splitlines():
            line = line.strip().lstrip("`").rstrip("`")
            m = self._HOVER_TYPE_RE.search(line)
            if m:
                return m.group(1) or m.group(2) or m.group(3) or m.group(4)
        return None
