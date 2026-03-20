# Rust Core Setup

## Installation
For development (recommended):
```bash
cd rust-core
maturin develop --release
```

This creates an editable install that links directly to your source code.

## Usage
```python
from rust_core import PyIndexManager, PySearchEngine, PyIngestionEngine, PyOntologyValidator

# Create index manager
im = PyIndexManager('/path/to/index')

# Use other components
se = PySearchEngine()
ie = PyIngestionEngine()  
ov = PyOntologyValidator()
```
