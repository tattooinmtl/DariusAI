"""brain_graph - the in-memory graph of the DariusAI brain (no networkx).

Consolidates graph data structures, layout computation, and traversal algorithms
away from the persistence layer in BrainStore.
"""

from .graph import BrainGraph

__all__ = ["BrainGraph"]
