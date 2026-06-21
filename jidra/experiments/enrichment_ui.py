"""Real-time ANSI live progress display for multi-agent enrichment."""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Any


class AgentProgressUI:
    """
    Live ANSI progress display for parallel enrichment agents.
    Shows each agent's status, elapsed time, and confidence in a fixed block.
    Degrades to scrolling log when not a TTY.
    """

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    CURSOR_UP = "\033[A"
    ERASE_LINE = "\033[2K"
    RESET = "\033[0m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    DIM = "\033[2m"

    def __init__(self, title: str, slots: list[str]):
        """
        Args:
            title: Display title (e.g. "Class Mode | DefaultSearchServiceImpl | 24 agents")
            slots: List of agent names (typically method names) in display order
        """
        self.title = title
        self.slots = slots
        self.state = {n: "queued" for n in slots}
        self.phase = {n: "" for n in slots}
        self.elapsed = {n: 0.0 for n in slots}
        self.conf = {n: None for n in slots}
        self.error = {n: None for n in slots}
        self.reason = {n: None for n in slots}
        self._start_time = {n: None for n in slots}
        self._global_start = time.monotonic()
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._frame = 0
        self._is_tty = sys.stdout.isatty()
        self._lines_drawn = 0
        self._render_thread = None
        self._enriched = 0
        self._failed = 0

    def start(self):
        """Initialize display and start render thread."""
        if self._is_tty:
            self._print_header()
            self._render_thread = threading.Thread(target=self._render_loop, daemon=True)
            self._render_thread.start()

    def update(
        self,
        name: str,
        state: str,
        phase: str = "",
        conf: float | None = None,
        error: str | None = None,
        reason: str | None = None,
    ) -> None:
        """
        Update an agent's state. Thread-safe.
        state: 'queued', 'extracting', 'judging', 'enriched', 'failed'
        """
        with self._lock:
            if name not in self.state:
                return  # Ignore unknown names
            if state == self.state.get(name):
                # Same state, just update phase/elapsed
                pass
            else:
                # State transition
                if state in ("extracting", "judging") and self._start_time[name] is None:
                    self._start_time[name] = time.monotonic()
                if state == "enriched":
                    self._enriched += 1
                if state == "failed":
                    self._failed += 1
            self.state[name] = state
            self.phase[name] = phase
            if conf is not None:
                self.conf[name] = conf
            if error is not None:
                self.error[name] = error
            if reason is not None:
                self.reason[name] = reason

    def stop(self, stats: dict[str, Any]) -> None:
        """Finalize display and print summary."""
        self._done.set()
        if self._render_thread:
            self._render_thread.join(timeout=1.0)
        if self._is_tty:
            self._print_summary(stats)

    def _print_header(self) -> None:
        """Print the title and column headers."""
        print(self.title)
        print("─" * 72)

    def _print_summary(self, stats: dict[str, Any]) -> None:
        """Print final summary line."""
        elapsed_s = time.monotonic() - self._global_start
        pending = len(self.slots) - self._enriched - self._failed
        summary = f" {self._enriched} enriched  {self._failed} failed  {pending} pending  │  elapsed {elapsed_s:.1f}s"
        print("─" * 72)
        print(summary)

    def _render_loop(self) -> None:
        """Background render loop at ~10Hz."""
        while not self._done.is_set():
            self._render_frame()
            time.sleep(0.1)
        # Final render
        self._render_frame()

    def _render_frame(self) -> None:
        """Render all agent rows; use ANSI to overwrite previous block."""
        with self._lock:
            # Build the block
            lines = []
            for i, name in enumerate(self.slots):
                lines.append(self._format_row(i, name))

            block = "\n".join(lines)

        # Use ANSI to replace previous block
        if self._lines_drawn > 0:
            # Move cursor up and erase previous lines
            sys.stdout.write((self.CURSOR_UP + self.ERASE_LINE) * self._lines_drawn)

        sys.stdout.write(block + "\n")
        sys.stdout.flush()

        self._lines_drawn = len(lines)
        self._frame += 1

    def _format_row(self, index: int, name: str) -> str:
        """Format a single agent status row."""
        state = self.state[name]
        elapsed = time.monotonic() - self._start_time[name] if self._start_time[name] else 0.0

        # Icon and color
        if state == "queued":
            icon = "●"
            color = ""
        elif state == "extracting":
            spinner_char = self.SPINNER[self._frame % len(self.SPINNER)]
            icon = spinner_char
            color = ""
        elif state == "judging":
            spinner_char = self.SPINNER[self._frame % len(self.SPINNER)]
            icon = spinner_char
            color = ""
        elif state == "enriched":
            icon = "✓"
            color = self.GREEN
        elif state == "failed":
            icon = "✗"
            color = self.RED
        elif state == "rejected":
            icon = "✗"
            color = self.RED
        else:
            icon = "–"
            color = self.DIM

        # Truncate name to fit
        name_display = name[:25].ljust(25)

        # Elapsed time
        if state == "queued":
            elapsed_str = "—"
        else:
            elapsed_str = f"{elapsed:.1f}s"

        # Detailed error for failed state right after status
        failed_error_str = ""
        if state == "failed":
            err = self.error.get(name) or ""
            if err:
                failed_error_str = f"  {self.RED}{err[:25]}{self.RESET}"

        # Rejection reason (optional)
        reason_str = ""
        if state == "rejected":
            reason_val = self.reason.get(name) or ""
            if reason_val:
                reason_str = f"  {self.RED}{reason_val[:30]}{self.RESET}"

        # Confidence
        conf_str = ""
        if self.conf[name] is not None:
            conf_str = f"  conf={self.conf[name]:.2f}"

        # Error
        error_str = ""
        if self.error[name]:
            error_str = f"  {self.error[name][:20]}"

        # Build row: [index] name icon status elapsed conf error
        row = f" [{index + 1:2}]  {name_display}  {color}{icon}{self.RESET}  {state:12}{failed_error_str}  {elapsed_str:>6}{reason_str}{conf_str}{error_str}"
        return row
