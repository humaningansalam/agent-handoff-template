from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .graph_model import GraphEdge, GraphNode, GraphSnapshot


@dataclass(frozen=True)
class GraphMatch:
    node_id: str
    score: float
    reason: str


class GraphIndex:
    def __init__(self, snapshot: GraphSnapshot) -> None:
        self.snapshot = snapshot
        self.node_by_id: dict[str, GraphNode] = {node.id: node for node in snapshot.nodes}
        self.outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
        self.incoming: dict[str, list[GraphEdge]] = defaultdict(list)
        for edge in snapshot.edges:
            self.outgoing[edge.from_id].append(edge)
            self.incoming[edge.to_id].append(edge)

    def exact_matches(self, terms: set[str]) -> list[GraphMatch]:
        matches: list[GraphMatch] = []
        lowered = {term.lower() for term in terms if term}
        if not lowered:
            return matches
        for node in self.snapshot.nodes:
            values = _node_values(node)
            joined = " ".join(values).lower()
            if any(term in values for term in terms):
                matches.append(GraphMatch(node.id, 1.0, f"graph exact {node.kind}"))
            elif any(term in joined for term in lowered if len(term) >= 3):
                matches.append(GraphMatch(node.id, 0.5, f"graph lexical {node.kind}"))
        return sorted(matches, key=lambda item: (-item.score, item.node_id))

    def neighborhood(self, seed_ids: set[str], *, depth: int = 1) -> tuple[set[str], list[GraphEdge]]:
        seen = set(seed_ids)
        frontier = set(seed_ids)
        edges: list[GraphEdge] = []
        for _ in range(depth):
            next_frontier: set[str] = set()
            for node_id in sorted(frontier):
                related = [*self.outgoing.get(node_id, []), *self.incoming.get(node_id, [])]
                for edge in related:
                    edges.append(edge)
                    for candidate in (edge.from_id, edge.to_id):
                        if candidate not in seen:
                            seen.add(candidate)
                            next_frontier.add(candidate)
            frontier = next_frontier
        return seen, sorted(edges, key=lambda edge: (edge.kind, edge.from_id, edge.to_id, edge.assertion, edge.source))


def _node_values(node: GraphNode) -> list[str]:
    values: list[str] = [node.id, node.kind]
    values.extend(str(value) for value in node.identity.values() if isinstance(value, str))
    facts = node.facts
    for namespace in facts.values():
        if isinstance(namespace, dict):
            for value in namespace.values():
                if isinstance(value, str):
                    values.append(value)
                elif isinstance(value, list):
                    values.extend(str(item) for item in value)
    return values
