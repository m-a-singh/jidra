"""Terminal UI helpers for `jidra up` / `jidra process`.

Wraps `rich` for a Claude-Code-style CLI: boxed banners, colored step
headers, spinners, progress bars, and a final "ready" panel. Degrades to
plain `print()`/`input()` if `rich` isn't installed, so the CLI never hard
crashes on a missing optional dependency.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
    from rich import box

    RICH = True
except ImportError:  # pragma: no cover - rich is a declared dependency
    RICH = False

ACCENT = "bold cyan"
OK = "bold green"
WARN = "bold yellow"
ERR = "bold red"
DIM = "dim"

console = Console(highlight=False) if RICH else None


def banner(title: str, subtitle: str = "") -> None:
    if not RICH:
        print(f"\n{'=' * 80}\n{title}\n{'=' * 80}\n")
        if subtitle:
            print(subtitle)
        return
    body = f"[{ACCENT}]{title}[/{ACCENT}]"
    if subtitle:
        body += f"\n[{DIM}]{subtitle}[/{DIM}]"
    console.print(Panel(body, box=box.ROUNDED, border_style="cyan", padding=(1, 3)))


def section(step: int, total: int, title: str) -> None:
    if not RICH:
        print(f"\n[{step}/{total}] {title.upper()}")
        return
    console.print(f"\n[{ACCENT}]●[/{ACCENT}] [bold]{step}/{total}[/bold]  {title}")


def success(msg: str) -> None:
    if not RICH:
        print(f"✓ {msg}")
        return
    console.print(f"  [{OK}]✓[/{OK}] {msg}")


def info(msg: str) -> None:
    if not RICH:
        print(f"  {msg}")
        return
    console.print(f"  [{DIM}]{msg}[/{DIM}]")


def warn(msg: str) -> None:
    if not RICH:
        print(f"! {msg}")
        return
    console.print(f"  [{WARN}]![/{WARN}] {msg}")


def error(msg: str) -> None:
    if not RICH:
        print(f"✗ {msg}")
        return
    console.print(f"  [{ERR}]✗[/{ERR}] {msg}")


@contextmanager
def spinner(message: str) -> Iterator[_RichSpinnerHandle | _PlainSpinnerHandle]:
    """Animated spinner for indeterminate work. `.update(text)` to change
    the visible message mid-task (e.g. a live file/class counter)."""
    if not RICH:
        print(f"  ⠋ {message}...")
        yield _PlainSpinnerHandle(message)
        return
    with console.status(f"[{ACCENT}]{message}[/{ACCENT}]", spinner="dots") as status:
        yield _RichSpinnerHandle(status)


class _RichSpinnerHandle:
    def __init__(self, status):
        self._status = status

    def update(self, text: str) -> None:
        self._status.update(f"[{ACCENT}]{text}[/{ACCENT}]")


class _PlainSpinnerHandle:
    def __init__(self, message: str):
        self._last = message

    def update(self, text: str) -> None:
        if text != self._last:
            print(f"  ... {text}")
            self._last = text


@contextmanager
def progress_bar(description: str, total: int | None = None) -> Iterator[tuple]:
    """Determinate (or indeterminate, if total is None) progress bar.
    Yields (progress, task_id) so the caller can call progress.update(task,
    advance=1) or progress.update(task, completed=n)."""
    if not RICH:
        print(f"  {description}...")
        yield (_PlainProgress(), 0)
        return
    columns = [
        SpinnerColumn(style=ACCENT),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    ]
    with Progress(*columns, console=console, transient=False) as progress:
        task = progress.add_task(description, total=total)
        yield (progress, task)


class _PlainProgress:
    def update(self, _task, **kwargs) -> None:
        pass

    def add_task(self, *_args, **_kwargs):
        return 0


def kv_panel(title: str, rows: list[tuple[str, str]]) -> None:
    if not RICH:
        print(f"\n{title}")
        for label, value in rows:
            print(f"   {label}: {value}")
        return
    table = Table.grid(padding=(0, 2))
    table.add_column(style=f"{DIM}", justify="right")
    table.add_column(style="bold white")
    for label, value in rows:
        table.add_row(label, value)
    console.print(
        Panel(
            table, title=f"[{OK}]{title}[/{OK}]", box=box.ROUNDED, border_style="green"
        )
    )


def rule(text: str = "") -> None:
    if not RICH:
        print(f"\n{'-' * 80}")
        return
    console.print(
        f"[{DIM}]{'─' * 60}[/{DIM}] {text}" if text else f"[{DIM}]{'─' * 60}[/{DIM}]"
    )


def prompt(
    prompt_text: str,
    default: str = "",
    choices: list[str] | None = None,
    optional: bool = False,
) -> str:
    if not RICH:
        return _plain_prompt(prompt_text, default, choices, optional)
    while True:
        response = (
            Prompt.ask(
                f"[bold cyan]?[/bold cyan] {prompt_text}",
                default=default or None,
                choices=choices,
                show_choices=bool(choices),
                show_default=bool(default),
            )
            or ""
        ).strip()
        if not response:
            if default:
                return default
            if optional:
                return ""
            console.print("  [dim]Please enter a value.[/dim]")
            continue
        return response


def _plain_prompt(
    prompt_text: str,
    default: str,
    choices: list[str] | None,
    optional: bool,
) -> str:
    while True:
        label = f"{prompt_text} [{default}]: " if default else f"{prompt_text}: "
        response = input(label).strip()
        if not response:
            if default:
                return default
            if optional:
                return ""
            print("Please enter a value.")
            continue
        if choices and response not in choices:
            print(f"Invalid option. Choose from: {', '.join(choices)}")
            continue
        return response


def prompt_yn(prompt_text: str, default: bool = False) -> bool:
    if not RICH:
        default_str = "Y/n" if default else "y/N"
        while True:
            response = input(f"{prompt_text} [{default_str}]: ").strip().lower()
            if not response:
                return default
            if response in ("y", "yes"):
                return True
            if response in ("n", "no"):
                return False
            print("Please enter 'y' or 'n'.")
    return Confirm.ask(f"[bold cyan]?[/bold cyan] {prompt_text}", default=default)
