fn main() {
    // If pyo3-build-config is available, use it; otherwise skip
    // This is only needed for cross-compilation or specific Python setups
    println!("cargo:rerun-if-changed=build.rs");
}
