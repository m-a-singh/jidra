"""
Graph validation and filtering against Spring Boot actuator beans.
Removes phantom edges to confirmed non-bean classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .models import CallSite, Graph, MethodEntry, ResolvedCallEdge


@dataclass
class ValidationReport:
    """Summary of graph validation and filtering."""

    total_classes: int
    confirmed_beans: int
    unconfirmed_classes: list[str] = field(default_factory=list)
    edges_before: int = 0
    edges_after: int = 0
    edges_removed: int = 0
    callsites_upgraded: int = 0
    removed_edges: list[tuple[str, str]] = field(default_factory=list)


def parse_actuator_beans(beans_response: dict) -> set[str]:
    """
    Extract the set of confirmed bean class names from /actuator/beans response.

    Spring Boot actuator response has nested structure:
    {
      "contexts": {
        "application": {
          "beans": {
            "someBeanName": { "type": "com.example.SomeService", ... },
            ...
          }
        }
      }
    }

    Args:
        beans_response: Raw /actuator/beans dict from Spring Boot.

    Returns:
        Set of full class names (e.g., {"com.example.SomeService", "org.springframework.boot...."}).
    """
    confirmed = set()
    contexts = beans_response.get("contexts", {})

    for context_name, context_data in contexts.items():
        beans = context_data.get("beans", {})
        for bean_name, bean_info in beans.items():
            if isinstance(bean_info, dict) and "type" in bean_info:
                bean_type = bean_info["type"]
                if bean_type:
                    confirmed.add(bean_type)

    return confirmed


def validate_graph(
    graph: Graph,
    confirmed_beans: set[str],
    no_filter: bool = False,
    verbose: bool = True,
) -> tuple[Graph, ValidationReport]:
    """
    Validate graph against confirmed beans and optionally filter it.

    Filtering logic:
    1. Identify confirmed_class_ids from ClassEntry.full_name
    2. Identify confirmed_method_ids from MethodEntry.class_id
    3. Remove ResolvedCallEdge where callee_method_id not in confirmed_method_ids
    4. Remove CallSite where all resolved_candidates point to non-confirmed methods
    5. Upgrade unresolved CallSites where receiver_type matches a confirmed bean

    Args:
        graph: Input graph (not mutated).
        confirmed_beans: Set of full class names from actuator.
        no_filter: If True, annotate only but don't remove edges.
        verbose: Print progress updates.

    Returns:
        Tuple of (filtered_graph, ValidationReport).
    """
    report = ValidationReport(
        total_classes=len(graph.classes),
        confirmed_beans=len(confirmed_beans),
    )

    if verbose:
        print(f"  • Analyzing {len(graph.classes)} classes against {len(confirmed_beans)} confirmed beans", flush=True)

    # Build maps
    confirmed_class_ids = {
        cls.id for cls in graph.classes
        if cls.full_name in confirmed_beans
    }
    confirmed_method_ids = {
        method.id for method in graph.methods
        if method.class_id in confirmed_class_ids
    }

    unconfirmed_class_ids = {
        cls.id for cls in graph.classes
        if cls.id not in confirmed_class_ids
    }

    report.unconfirmed_classes = sorted([
        cls.full_name for cls in graph.classes
        if cls.id in unconfirmed_class_ids
    ])

    if verbose:
        print(f"  • Found {len(confirmed_class_ids)} confirmed classes, {len(unconfirmed_class_ids)} unconfirmed", flush=True)

    # Counts before filtering
    report.edges_before = len(graph.resolved_call_edges)

    if no_filter:
        # Annotate-only mode: keep all edges, just report what *would* be removed
        edges_to_remove = [
            edge for edge in graph.resolved_call_edges
            if edge.callee_method_id not in confirmed_method_ids
        ]
        report.edges_removed = len(edges_to_remove)
        report.removed_edges = [
            (edge.caller_method_id, edge.callee_method_id)
            for edge in edges_to_remove
        ]
        # Return original graph, but with report of what would be removed
        report.edges_after = report.edges_before
        return graph, report

    # Filter edges: keep only those pointing to confirmed methods
    filtered_edges = [
        edge for edge in graph.resolved_call_edges
        if edge.callee_method_id in confirmed_method_ids
    ]
    report.edges_removed = len(graph.resolved_call_edges) - len(filtered_edges)
    report.removed_edges = [
        (edge.caller_method_id, edge.callee_method_id)
        for edge in graph.resolved_call_edges
        if edge.callee_method_id not in confirmed_method_ids
    ]

    # Filter callsites: remove those where all resolved candidates are unconfirmed
    filtered_callsites = []
    for callsite in graph.callsites:
        # Keep if: unresolved, has no resolved_candidates, or at least one confirmed
        if not callsite.resolved_candidates:
            # Unresolved callsite, keep it for debugging
            filtered_callsites.append(callsite)
        elif any(mid in confirmed_method_ids for mid in callsite.resolved_candidates):
            # At least one candidate is confirmed, keep it
            filtered_callsites.append(callsite)
        # else: all resolved candidates unconfirmed, remove

    # Upgrade unresolved callsites where receiver type matches a confirmed bean
    upgraded = 0
    for callsite in filtered_callsites:
        if (
            callsite.resolution_status == "unresolved_receiver"
            and callsite.receiver_type_normalized
            and callsite.receiver_type_normalized in confirmed_beans
        ):
            callsite.resolution_status = "actuator_resolved"
            upgraded += 1

    report.callsites_upgraded = upgraded
    report.edges_after = len(filtered_edges)

    if verbose:
        pct = round(100 * report.edges_removed / max(1, report.edges_before), 1)
        print(f"  • Removed {report.edges_removed} phantom edges ({pct}%)", flush=True)
        print(f"  • Filtered {len(graph.callsites) - len(filtered_callsites)} callsites", flush=True)
        if upgraded > 0:
            print(f"  • Upgraded {upgraded} callsites to actuator_resolved", flush=True)

    # Create filtered graph
    filtered_graph = Graph(
        classes=graph.classes,  # keep all classes for context
        methods=graph.methods,  # keep all methods for context
        fields=graph.fields,
        callsites=filtered_callsites,
        inheritance_edges=graph.inheritance_edges,
        resolved_call_edges=filtered_edges,
    )

    return filtered_graph, report
