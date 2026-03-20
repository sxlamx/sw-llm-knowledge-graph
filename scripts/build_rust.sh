#!/usr/bin/env bash
# Build the Rust core PyO3 extension and install it into the active Python environment.
# Run from the repo root: ./scripts/build_rust.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUST_CORE="$REPO_ROOT/rust-core"

# Install Rust toolchain if missing
if ! command -v cargo &>/dev/null; then
  echo "Rust not found — installing via rustup..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
  source "$HOME/.cargo/env"
fi

# Install maturin if missing
if ! command -v maturin &>/dev/null; then
  echo "maturin not found — installing..."
  pip install maturin
fi

echo "Building rust-core..."
cd "$RUST_CORE"
maturin develop --release

echo ""
echo "Verifying import..."
python -c "import rust_core; print('rust_core imported OK')"
echo "Build complete."
