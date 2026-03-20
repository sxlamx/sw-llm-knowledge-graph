# Re-export all classes from the Rust extension module
try:
    # Try to import from the compiled extension 
    from rust_core import (
        PyIndexManager,
        PySearchEngine, 
        PyIngestionEngine,
        PyOntologyValidator,
        compute_blake3,
        hash_matches,
        resolve_entity,
        bfs_reachable,
        find_shortest_path,
        export_graph,
    )
except ImportError:
    # Fallback if extension not available
    PyIndexManager = None
    PySearchEngine = None
    PyIngestionEngine = None  
    PyOntologyValidator = None
    compute_blake3 = None
    hash_matches = None
    resolve_entity = None
    bfs_reachable = None
    find_shortest_path = None
    export_graph = None

__all__ = [
    "PyIndexManager",
    "PySearchEngine", 
    "PyIngestionEngine",
    "PyOntologyValidator",
    "compute_blake3",
    "hash_matches",
    "resolve_entity", 
    "bfs_reachable",
    "find_shortest_path", 
    "export_graph",
]