#!/usr/bin/env python3
"""Test script to verify the rust_core package imports work correctly."""

def test_imports():
    """Test that all expected classes can be imported and instantiated."""
    print("Testing rust_core package imports...")
    
    # Test imports
    from rust_core import PyIndexManager, PySearchEngine, PyIngestionEngine, PyOntologyValidator
    
    # Test instantiation
    import tempfile
    temp_dir = tempfile.mkdtemp()
    
    im = PyIndexManager(temp_dir)
    se = PySearchEngine()
    ie = PyIngestionEngine()
    ov = PyOntologyValidator()
    
    print("✅ All classes imported and instantiated successfully!")
    print(f"✅ IndexManager created with path: {temp_dir}")
    print("✅ Import test PASSED")

if __name__ == "__main__":
    test_imports()
