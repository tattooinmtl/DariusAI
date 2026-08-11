"""brain_graph - NetworkX graph algorithms and layout for the DariusAI brain.

Consolidates graph data structures, layout computation, and traversal algorithms
away from the persistence layer in BrainStore.
"""

from .graph import BrainGraph

__all__ = ["BrainGraph"]
