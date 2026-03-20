fn main() {
    // Use pyo3-build-config to set up Python properly
    pyo3_build_config::use_pyo3_cfgs();
    println!("cargo:rerun-if-changed=build.rs");
}
