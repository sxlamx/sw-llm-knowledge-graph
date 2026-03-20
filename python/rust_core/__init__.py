# Re-export all classes from the Rust extension module
from .rust_core import (
    IndexManager,
    PySearchEngine,
    PyIngestionEngine,
    PyOntologyValidator,
    compute_blake3,
    check_hash_matches,
    resolve_entity,
    check_bfs_reachable,
    check_shortest_path,
    export_graph
)

# Provide aliases for more intuitive naming if desired
PyIndexManager = IndexManager
