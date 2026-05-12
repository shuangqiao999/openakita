"""
Task Graph — DAG-based task dependency support for multi-agent scheduling.

Defines ``TaskNode`` and ``TaskGraph`` for expressing and validating
task dependencies in delegate_parallel.  ``depends_on`` lists task IDs
that must complete before this node can be scheduled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class TaskNode:
    """A single unit of work in a multi-agent task graph."""

    task_id: str
    agent_id: str
    message: str
    reason: str = ""
    context: str = ""
    depends_on: list[str] = field(default_factory=list)
    priority: int = 2
    timeout_seconds: float = 0.0
    retry_count: int = 0
    max_retries: int = 1
    completed: bool = False
    result: str = ""


class TaskGraph:
    """Validate and traverse a DAG of TaskNodes.

    Detects circular dependencies and provides topological layers
    for parallel scheduling.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, TaskNode] = {}

    def add_node(self, node: TaskNode) -> None:
        if node.task_id in self._nodes:
            raise ValueError(f"Duplicate task_id: {node.task_id}")
        self._nodes[node.task_id] = node

    def validate(self) -> list[str]:
        """Return list of errors, empty means valid.

        Checks: no circular deps, all depends_on targets exist.
        """
        errors: list[str] = []
        for node in self._nodes.values():
            for dep_id in node.depends_on:
                if dep_id not in self._nodes:
                    errors.append(f"task '{node.task_id}' depends on missing task '{dep_id}'")
                if dep_id == node.task_id:
                    errors.append(f"task '{node.task_id}' depends on itself")
        cycle_report = self._detect_cycles()
        if cycle_report:
            errors.extend(cycle_report)
        return errors

    def _detect_cycles(self) -> list[str]:
        visited: set[str] = set()
        recursion_stack: set[str] = set()
        errors: list[str] = []

        def dfs(task_id: str) -> bool:
            visited.add(task_id)
            recursion_stack.add(task_id)
            node = self._nodes.get(task_id)
            if node:
                for dep_id in node.depends_on:
                    if dep_id not in visited:
                        if dfs(dep_id):
                            return True
                    elif dep_id in recursion_stack:
                        errors.append(f"circular dependency detected involving '{task_id}' -> '{dep_id}'")
                        return True
            recursion_stack.discard(task_id)
            return False

        for tid in self._nodes:
            if tid not in visited:
                dfs(tid)
        return errors

    def topological_layers(self) -> list[list[str]]:
        """Return tasks grouped into layers where each layer can run in parallel.

        Layer 0 has no dependencies, layer 1 depends on layer 0, etc.
        """
        in_degree: dict[str, int] = {tid: len(node.depends_on) for tid, node in self._nodes.items()}
        dependants: dict[str, list[str]] = {tid: [] for tid in self._nodes}
        for node in self._nodes.values():
            for dep_id in node.depends_on:
                if dep_id in dependants:
                    dependants[dep_id].append(node.task_id)

        ready = [tid for tid, deg in in_degree.items() if deg == 0]
        layers: list[list[str]] = []
        while ready:
            layers.append(sorted(ready))
            next_ready: list[str] = []
            for tid in ready:
                for dep_id in dependants.get(tid, []):
                    in_degree[dep_id] -= 1
                    if in_degree[dep_id] == 0:
                        next_ready.append(dep_id)
            ready = next_ready
        return layers

    def is_ready(self, task_id: str, completed_ids: set[str]) -> bool:
        node = self._nodes.get(task_id)
        if node is None:
            return False
        return all(dep in completed_ids for dep in node.depends_on)

    def is_all_complete(self, completed_ids: set[str]) -> bool:
        return len(completed_ids) == len(self._nodes)

    def get_ready(self, completed_ids: set[str]) -> list[TaskNode]:
        return [
            node
            for node in self._nodes.values()
            if not node.completed and self.is_ready(node.task_id, completed_ids)
        ]

    def mark_complete(self, task_id: str, result: str = "") -> None:
        node = self._nodes.get(task_id)
        if node:
            node.completed = True
            node.result = result

    def get_node(self, task_id: str) -> TaskNode | None:
        return self._nodes.get(task_id)

    @property
    def nodes(self) -> dict[str, TaskNode]:
        return self._nodes

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @staticmethod
    def from_tasks_list(
        tasks: list[dict],
        *,
        base_agent: str = "default",
    ) -> TaskGraph:
        """Build a TaskGraph from a flat list of task dicts.

        Task dict structure:
            {
                "agent_id": str,
                "message": str,
                "reason": str (optional),
                "context": str (optional),
                "depends_on": list[str] (optional),
                "id": str (optional, auto-generated),
                "priority": int (optional, default 2),
            }
        """
        graph = TaskGraph()
        for task in tasks:
            tid = (task.get("id") or task.get("task_id") or "").strip()
            if not tid:
                tid = f"task_{uuid.uuid4().hex[:8]}"
            node = TaskNode(
                task_id=tid,
                agent_id=(task.get("agent_id") or base_agent).strip(),
                message=(task.get("message") or task.get("task") or "").strip(),
                reason=(task.get("reason") or "").strip(),
                context=(task.get("context") or "").strip(),
                depends_on=list(task.get("depends_on") or []),
                priority=int(task.get("priority", 2)),
                timeout_seconds=float(task.get("timeout_seconds", 0)),
                max_retries=int(task.get("max_retries", 1)),
            )
            graph.add_node(node)
        return graph
