from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import queue
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .enrichment_agent import MethodEnrichmentAgent
from .enrichment_judge import EnrichmentJudge
from .context_builder import build_method_context
from .extractor import Graph
from .models import MethodEntry, ClassEntry

logger = logging.getLogger(__name__)


def _enrich_one_method_in_process(
    method_id: str,
    graph_json_path: str,
    extraction_model: str,
    judge_model: str,
    judge_fallback_model: str,
    max_retries: int,
    judge_threshold: float,
    extraction_threshold: float,
    use_judge: bool = False,
    ui_queue: multiprocessing.Queue | None = None,
) -> dict[str, Any]:
    """
    Worker function for ProcessPoolExecutor.
    Runs in a separate process to enrich a single method.
    Note: Must be at module level for pickling.
    """
    try:

        # Load graph from temp JSON
        with open(graph_json_path, 'r') as f:
            records = json.load(f)

        # Reconstruct graph and find the method
        method_entry = None
        classes = []
        methods = []

        for record in records:
            if record.get("type") == "method":
                data = record.get("payload", record.get("data", {}))
                m = MethodEntry(
                    id=data.get("id"),
                    class_id=data.get("class_id"),
                    class_full_name=data.get("class_full_name"),
                    method_name=data.get("method_name"),
                    return_type=data.get("return_type", ""),
                    parameter_types=data.get("parameter_types", []),
                    parameter_names=data.get("parameter_names", []),
                    signature=data.get("signature", ""),
                    file_path=data.get("file_path", ""),
                    start_line=data.get("start_line", 0),
                    end_line=data.get("end_line", 0),
                    source=data.get("source", ""),
                    class_context=data.get("class_context", {}),
                    annotations=data.get("annotations", []),
                    local_variable_types=data.get("local_variable_types", {}),
                    field_reads=data.get("field_reads", []),
                    field_writes=data.get("field_writes", []),
                    is_endpoint=data.get("is_endpoint", False),
                    http_method=data.get("http_method"),
                    route=data.get("route"),
                    controller_route=data.get("controller_route"),
                    full_route=data.get("full_route"),
                    llm_summary=data.get("llm_summary"),
                    llm_business_intent=data.get("llm_business_intent"),
                    llm_risk_notes=data.get("llm_risk_notes"),
                    llm_summary_confidence=data.get("llm_summary_confidence", 0.0),
                    llm_confidence=data.get("llm_confidence", 0.0),
                    llm_external_calls=data.get("llm_external_calls", []),
                    analysis_status=data.get("analysis_status", "pending"),
                    analysis_retry_count=data.get("analysis_retry_count", 0),
                    last_judge_feedback=data.get("last_judge_feedback"),
                )
                methods.append(m)
                if data.get("id") == method_id:
                    method_entry = m
            elif record.get("type") == "class":
                data = record.get("payload", record.get("data", {}))
                # Only include classes if they have all required fields
                if all(k in data for k in ["id", "package_name", "name", "full_name", "file_path", "start_line", "end_line"]):
                    classes.append(ClassEntry(**data))

        if not method_entry:
            return {"success": False, "reason": "method_not_found"}

        # Emit extracting event
        if ui_queue:
            ui_queue.put({
                "name": method_entry.method_name,
                "state": "extracting",
                "phase": "extract",
            })

        # Create LLM client for this worker
        try:
            from llm_client import LLMClient
        except ImportError:
            logger.error("llm_client package not found")
            return {"success": False, "reason": "llm_client_import_failed"}
        llm = LLMClient()

        # Build minimal graph
        graph = Graph(
            classes=classes,
            methods=methods,
            fields=[],
            callsites=[],
            inheritance_edges=[],
            resolved_call_edges=[],
        )

        # Create and run enrichment for this method
        orch = EnrichmentOrchestrator(
            graph=graph,
            llm_client=llm,
            extraction_model=extraction_model,
            judge_model=judge_model,
            judge_fallback_model=judge_fallback_model,
            max_workers=1,
            max_retries=max_retries,
            judge_threshold=judge_threshold,
            extraction_threshold=extraction_threshold,
            use_judge=use_judge,
        )

        result = asyncio.run(orch.enrich_method(method_entry))

        if result.get("success"):
            enrichment = result.get("enrichment", {})
            if ui_queue:
                ui_queue.put({
                    "name": method_entry.method_name,
                    "state": "enriched",
                    "conf": enrichment.get("llm_summary_confidence"),
                })
            return {
                "success": True,
                "method": {
                    "llm_summary": enrichment.get("llm_summary"),
                    "llm_business_intent": enrichment.get("llm_business_intent"),
                    "llm_risk_notes": enrichment.get("llm_risk_notes"),
                    "llm_confidence": enrichment.get("llm_confidence", enrichment.get("llm_summary_confidence", 0.0)),
                    "llm_summary_confidence": enrichment.get("llm_summary_confidence"),
                    "llm_external_calls": [],
                    "analysis_status": enrichment.get("analysis_status"),
                    "last_judge_feedback": enrichment.get("last_judge_feedback"),
                },
            }
        else:
            err_msg = result.get("reason", "unknown_error")
            feedback = result.get("feedback") or err_msg
            state = "rejected" if err_msg == "judge_rejected" else "failed"
            if ui_queue:
                ui_queue.put({
                    "name": method_entry.method_name,
                    "state": state,
                    "error": str(err_msg),
                    "reason": str(feedback),
                })
            return {"success": False, "reason": err_msg, "feedback": result.get("feedback")}
    except Exception as e:
        import traceback
        logger.error(f"Process worker error for {method_id}: {e}\n{traceback.format_exc()}")
        return {"success": False, "reason": f"exception:{str(e)}"}


class EnrichmentOrchestrator:
    """
    Orchestrates enrichment of the codebase graph.
    Manages agent pool, judgment, graph updates, and recursion.
    """

    def __init__(
        self,
        graph,
        llm_client,
        extraction_model: str = "ollama/gemma4:e4b",
        judge_model: str = "ollama/gemma2:2b",
        judge_fallback_model: str | None = None,
        max_workers: int = 4,
        max_retries: int = 3,
        judge_threshold: float = 0.85,
        extraction_threshold: float = 0.80,
        use_judge: bool = False,
        context_max_chars: int = 12000,
        progress_callback=None,
    ):
        self.graph = graph
        self.llm_client = llm_client
        self.extraction_model = extraction_model
        self.judge_model = judge_model
        self.judge_fallback_model = judge_fallback_model or extraction_model
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.judge_threshold = judge_threshold
        self.extraction_threshold = extraction_threshold
        self.use_judge = use_judge
        self.context_max_chars = context_max_chars
        self.progress_callback = progress_callback

        self.agent = MethodEnrichmentAgent(llm_client, extraction_model)
        self.judge = EnrichmentJudge(llm_client, judge_model, self.judge_fallback_model) if use_judge else None

        self.stats = {
            "total_methods": len(graph.methods),
            "enriched": 0,
            "failed": 0,
            "pending": len(graph.methods),
            "extraction_attempts": 0,
            "judge_rejections": 0,
            "avg_extraction_confidence": 0.0,
            "avg_judge_confidence": 0.0,
            "workers_started": 0,
            "workers_finished": 0,
        }

        self.processed_ids = set()
        self.extraction_confidence_sum = 0.0
        self.judge_confidence_sum = 0.0
        self._started_mono = time.monotonic()

    def _emit(self, event: str, **extra: Any) -> None:
        if not self.progress_callback:
            return
        payload = {
            "event": event,
            "elapsed_ms": int((time.monotonic() - self._started_mono) * 1000),
            **extra,
        }
        self.progress_callback(payload)

    async def enrich_method(self, method_entry) -> dict[str, Any]:
        """
        Enrich a single method: extract → judge → update.
        Returns enrichment result.
        """
        method_id = method_entry.id
        if method_id in self.processed_ids:
            logger.debug(f"Skipping already-processed method {method_id}")
            return {"skipped": True, "reason": "already_processed"}

        self.processed_ids.add(method_id)
        method_start = time.monotonic()
        self._emit(
            "method_started",
            method_id=method_id,
            signature=method_entry.signature,
            queue_pending=self.stats["pending"],
        )

        # Attempt extraction with retries
        extraction = None
        for attempt in range(self.max_retries):
            logger.info(f"Extracting {method_entry.signature} (attempt {attempt + 1}/{self.max_retries})")
            self._emit(
                "extraction_attempt",
                method_id=method_id,
                signature=method_entry.signature,
                attempt=attempt + 1,
                max_retries=self.max_retries,
            )
            context = build_method_context(self.graph, method_entry.id, max_chars=self.context_max_chars)
            result = await self.agent.extract(method_entry, context=context)

            if not result["success"]:
                logger.warning(f"Extraction attempt {attempt + 1} failed: {result.get('error')}")
                self._emit(
                    "extraction_failed_attempt",
                    method_id=method_id,
                    signature=method_entry.signature,
                    attempt=attempt + 1,
                    error=result.get("error", ""),
                )
                continue

            extraction = result["extraction"]
            self.stats["extraction_attempts"] += 1
            break

        if not extraction:
            logger.error(f"Failed to extract method {method_id} after {self.max_retries} attempts")
            self.stats["failed"] += 1
            self._emit(
                "method_failed",
                method_id=method_id,
                signature=method_entry.signature,
                reason="extraction_failed",
                duration_ms=int((time.monotonic() - method_start) * 1000),
            )
            return {"success": False, "reason": "extraction_failed"}

        # Validate extraction confidence
        extraction_confidence = extraction.get("confidence", 0.0)
        if extraction_confidence < self.extraction_threshold:
            logger.warning(
                f"Extraction confidence {extraction_confidence} below threshold "
                f"{self.extraction_threshold} for {method_entry.signature}"
            )

        judgment = {"feedback": ""}
        judge_acceptable = True
        judge_confidence = extraction_confidence
        if self.use_judge and self.judge:
            logger.info(f"Judging extraction for {method_entry.signature}")
            self._emit("judge_started", method_id=method_id, signature=method_entry.signature)
            judge_result = await self.judge.judge(method_entry, extraction)

            if not judge_result["success"]:
                logger.error(f"Judgment failed: {judge_result.get('error')}")
                self.stats["failed"] += 1
                self._emit(
                    "method_failed",
                    method_id=method_id,
                    signature=method_entry.signature,
                    reason="judgment_failed",
                    duration_ms=int((time.monotonic() - method_start) * 1000),
                )
                return {"success": False, "reason": "judgment_failed"}

            judgment = judge_result["judgment"]
            judge_acceptable = judgment.get("acceptable", False)
            judge_confidence = judgment.get("confidence", 0.0)

        self.extraction_confidence_sum += extraction_confidence
        self.judge_confidence_sum += judge_confidence

        # Decision logic
        if not judge_acceptable:
            logger.warning(f"Judge rejected extraction: {judgment.get('feedback')}")
            self.stats["judge_rejections"] += 1
            self._emit(
                "method_rejected",
                method_id=method_id,
                signature=method_entry.signature,
                feedback=judgment.get("feedback", ""),
                judge_confidence=judge_confidence,
                duration_ms=int((time.monotonic() - method_start) * 1000),
            )

            # Could retry with modified prompt here, but for v1 we accept feedback
            # In v2, could implement adaptive prompting
            return {
                "success": False,
                "reason": "judge_rejected",
                "feedback": judgment.get("feedback"),
            }

        # Update graph record
        enriched_method = replace(
            method_entry,
            llm_summary=extraction.get("summary", ""),
            llm_business_intent=extraction.get("business_intent", ""),
            llm_risk_notes=extraction.get("risk_notes", ""),
            llm_summary_confidence=extraction_confidence,
            llm_confidence=extraction_confidence,
            llm_external_calls=[],
            analysis_status="enriched",
            analysis_retry_count=0,
            last_judge_feedback=judgment.get("feedback", ""),
        )

        # Update graph
        self._update_graph_record(enriched_method)
        self.stats["enriched"] += 1
        self.stats["pending"] -= 1

        logger.info(f"✓ Enriched {method_entry.signature}")
        self._emit(
            "method_enriched",
            method_id=method_id,
            signature=method_entry.signature,
            extraction_confidence=extraction_confidence,
            judge_confidence=judge_confidence,
            duration_ms=int((time.monotonic() - method_start) * 1000),
        )

        return {
            "success": True,
            "enrichment": asdict(enriched_method),
            "discovered_methods": [],
        }

    def _update_graph_record(self, enriched_method) -> None:
        """Update method in graph."""
        for i, m in enumerate(self.graph.methods):
            if m.id == enriched_method.id:
                self.graph.methods[i] = enriched_method
                return

    def _discover_unresolved_calls(self, external_calls: list[dict]) -> list[str]:
        """Find methods not yet processed from external calls."""
        discovered = []

        for call in external_calls:
            receiver_type = call.get("receiver_type")
            method_name = call.get("name")

            if not receiver_type or not method_name:
                continue

            # Find matching methods in graph
            candidates = [
                m for m in self.graph.methods
                if (
                    m.class_full_name.endswith(receiver_type) or
                    m.class_full_name.split(".")[-1] == receiver_type
                ) and m.method_name == method_name and m.id not in self.processed_ids
            ]

            for candidate in candidates:
                if candidate.id not in self.processed_ids:
                    discovered.append(candidate.id)

        return discovered

    async def enrich_batch(self, method_ids: list[str] | None = None) -> dict[str, Any]:
        """
        Enrich methods in parallel using worker pool.
        If method_ids is None, enrich all methods.
        """
        if method_ids is None:
            method_ids = [m.id for m in self.graph.methods]
        self._started_mono = time.monotonic()
        self._emit(
            "enrichment_started",
            total_methods=len(method_ids),
            max_workers=self.max_workers,
            extraction_model=self.extraction_model,
            judge_model=self.judge_model,
        )

        queue = asyncio.Queue()
        for method_id in method_ids:
            await queue.put(method_id)

        async def worker(worker_id: int):
            self.stats["workers_started"] += 1
            self._emit("worker_started", worker_id=worker_id)
            while True:
                try:
                    method_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                # Find method in graph
                method_entry = next((m for m in self.graph.methods if m.id == method_id), None)
                if not method_entry:
                    logger.warning(f"Method not found: {method_id}")
                    continue

                try:
                    result = await self.enrich_method(method_entry)
                    if result.get("success") and result.get("discovered_methods"):
                        # Queue discovered methods
                        for discovered_id in result["discovered_methods"]:
                            if discovered_id not in self.processed_ids:
                                await queue.put(discovered_id)
                except Exception as e:
                    logger.error(f"Error enriching method {method_id}: {e}")
                    self.stats["failed"] += 1
                    self._emit(
                        "method_failed",
                        method_id=method_id,
                        signature=(method_entry.signature if method_entry else ""),
                        reason=f"worker_exception:{type(e).__name__}",
                    )
            self.stats["workers_finished"] += 1
            self._emit("worker_finished", worker_id=worker_id)

        # Run worker pool
        workers = [worker(i + 1) for i in range(self.max_workers)]
        await asyncio.gather(*workers)

        # Finalize stats
        if self.stats["enriched"] + self.stats["failed"] > 0:
            self.stats["avg_extraction_confidence"] = (
                self.extraction_confidence_sum / (self.stats["enriched"] + self.stats["failed"])
            )
            self.stats["avg_judge_confidence"] = (
                self.judge_confidence_sum / (self.stats["enriched"] + self.stats["failed"])
            )

        result = {
            "completed": True,
            "stats": self.stats,
            "methods_processed": len(self.processed_ids),
        }
        self._emit(
            "enrichment_finished",
            methods_processed=len(self.processed_ids),
            enriched=self.stats["enriched"],
            failed=self.stats["failed"],
            judge_rejections=self.stats["judge_rejections"],
            duration_ms=int((time.monotonic() - self._started_mono) * 1000),
        )
        return result

    def export_enriched_graph(self, output_path: Path) -> None:
        """Export enriched graph as JSONL."""
        from .exporter import export_jsonl, graph_records

        records = graph_records(self.graph)
        export_jsonl(output_path, records)
        logger.info(f"Exported enriched graph to {output_path}")

    def export_stats(self, output_path: Path) -> None:
        """Export enrichment statistics."""
        with output_path.open("w") as f:
            json.dump(self.stats, f, indent=2)
        logger.info(f"Exported stats to {output_path}")

    def _get_external_classes(self, method_ids: list[str]) -> set[str]:
        """Find unique external classes called by methods."""
        external = set()
        for cs in self.graph.callsites:
            if cs.caller_method_id in method_ids:
                # Exclude internal same-class calls
                if cs.resolution_status != "resolved_same_class" and cs.receiver_type_normalized:
                    # Only include classes that are in the indexed codebase
                    if any(c.full_name == cs.receiver_type_normalized for c in self.graph.classes):
                        external.add(cs.receiver_type_normalized)
        return external

    def enrich_class(self, class_name: str, output_path: Path, ui: Any = None) -> dict[str, Any]:
        """
        Spawn N worker processes for all methods in a class.
        Each process enriches one method independently.
        """
        from .exporter import graph_records

        methods = [m for m in self.graph.methods if class_name in m.class_full_name]
        if not methods:
            logger.warning(f"No methods found for class containing '{class_name}'")
            return {"success": False, "reason": "no_methods_found"}

        logger.info(f"Class mode: enriching {len(methods)} methods from class containing '{class_name}'")

        # Serialize graph to temp file for worker processes
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
            tmp_path = tmp.name
            json.dump(graph_records(self.graph), tmp)

        # Use Manager for cross-process queue
        manager = multiprocessing.Manager() if ui else None
        ui_queue = manager.Queue() if ui and manager else None
        relay_done = threading.Event()
        enriched_count = 0
        failed_count = 0

        try:
            # Start relay thread if UI enabled
            if ui and ui_queue:
                ui.start()

                def relay_events():
                    while not relay_done.is_set():
                        try:
                            event = ui_queue.get(timeout=0.05)
                            ui.update(
                                event["name"],
                                event["state"],
                                conf=event.get("conf"),
                                error=event.get("error"),
                                reason=event.get("reason"),
                            )
                        except queue.Empty:
                            pass

                relay_thread = threading.Thread(target=relay_events, daemon=True)
                relay_thread.start()

            # Spawn bounded process pool
            with ProcessPoolExecutor(max_workers=max(1, min(self.max_workers, len(methods)))) as executor:
                futures = {
                    executor.submit(
                        _enrich_one_method_in_process,
                        m.id,
                        tmp_path,
                        self.extraction_model,
                        self.judge_model,
                        self.judge_fallback_model,
                        self.max_retries,
                        self.judge_threshold,
                        self.extraction_threshold,
                        self.use_judge,
                        ui_queue,
                    ): m.id for m in methods
                }

                for future in as_completed(futures):
                    method_id = futures[future]
                    try:
                        result = future.result()
                        if result.get("success"):
                            enriched_count += 1
                            # Update graph with enriched method
                            enriched_data = result.get("method")
                            if enriched_data:
                                for i, m in enumerate(self.graph.methods):
                                    if m.id == method_id:
                                        # Reconstruct method entry from dict
                                        self.graph.methods[i] = replace(m, **enriched_data)
                                        break
                            logger.info(f"✓ Process enriched {method_id}")
                        else:
                            failed_count += 1
                            logger.warning(f"✗ Process failed for {method_id}: {result.get('reason')}")
                    except Exception as e:
                        failed_count += 1
                        logger.error(f"Worker process error for {method_id}: {e}")

            # Stop relay and UI
            if ui:
                relay_done.set()
                if relay_thread:
                    relay_thread.join(timeout=1.0)
                ui.stop({"enriched": enriched_count, "failed": failed_count, "total": len(methods)})

            # Export enriched graph
            self.export_enriched_graph(output_path)
            logger.info(f"Class mode complete: enriched {enriched_count}/{len(methods)} methods")
            return {"success": True, "enriched": enriched_count, "failed": failed_count, "total": len(methods)}
        finally:
            # Clean up temp file
            Path(tmp_path).unlink(missing_ok=True)

    def _get_methods_in_class(self, class_full_name: str) -> list[str]:
        """Get all method IDs in a class."""
        return [m.id for m in self.graph.methods if m.class_full_name == class_full_name]

    def enrich_flow(
        self,
        class_name: str,
        output_path: Path,
        max_depth: int = 2,
        max_fanout: int = 8,
    ) -> dict[str, Any]:
        """
        Flow mode: follow call graph with parallel fan-out.
        Starts at a class, finds external calls, spawns threads for each external class.
        """

        logger.info(f"Flow mode: starting from class containing '{class_name}', max_depth={max_depth}, max_fanout={max_fanout}")

        visited_classes = set()
        visited_methods = set()

        def enrich_flow_recursive(target_class_name: str, depth: int):
            if depth > max_depth or target_class_name in visited_classes:
                return

            visited_classes.add(target_class_name)
            methods = self._get_methods_in_class(target_class_name)
            if not methods:
                logger.warning(f"No methods found in class {target_class_name}")
                return

            # Enrich all methods in this class (serially)
            logger.info(f"Flow depth {depth}: enriching {len(methods)} methods in {target_class_name}")
            for method_id in methods:
                if method_id not in visited_methods:
                    visited_methods.add(method_id)
                    try:
                        method_entry = next((m for m in self.graph.methods if m.id == method_id), None)
                        if method_entry:
                            asyncio.run(self.enrich_method(method_entry))
                    except Exception as e:
                        logger.error(f"Error enriching {method_id}: {e}")

            # Find external classes to fan out to
            external_classes = self._get_external_classes(methods)
            if not external_classes:
                logger.debug(f"No external calls from {target_class_name}")
                return

            # Fan out to external classes (limited by max_fanout)
            external_to_process = list(external_classes)[:max_fanout]
            logger.info(f"Flow depth {depth}: found {len(external_classes)} external classes, processing {len(external_to_process)}")

            with ThreadPoolExecutor(max_workers=max_fanout) as executor:
                futures = [
                    executor.submit(enrich_flow_recursive, ext_class, depth + 1)
                    for ext_class in external_to_process
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Thread error in flow mode: {e}")

        # Start recursion from target class
        enrich_flow_recursive(class_name, 0)

        # Export enriched graph
        self.export_enriched_graph(output_path)
        logger.info(f"Flow mode complete: enriched {len(visited_methods)} methods")
        return {
            "success": True,
            "methods_enriched": len(visited_methods),
            "classes_visited": len(visited_classes),
        }
