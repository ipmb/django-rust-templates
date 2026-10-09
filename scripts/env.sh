#!/usr/bin/env bash
# Common environment for all project scripts. Source this, do not execute it.
#
# Rust is installed via rustup but its cargo/rustc shims are not present in
# ~/.cargo/bin on this machine, so we derive the active toolchain's bin dir.
# Sourcing this twice is harmless.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT="$ROOT"

RUST_BIN_DIR="$(dirname "$(rustup which cargo)")"
export PATH="$RUST_BIN_DIR:$HOME/.cargo/bin:$PATH"

if [ -d "$ROOT/.venv/bin" ]; then
  export PATH="$ROOT/.venv/bin:$PATH"
  export VIRTUAL_ENV="$ROOT/.venv"
fi

# Make the pinned Django checkout runnable and our shim installable.
export DJANGO_RUST_TEMPLATES=1

# The machine's ~/.config/pip/pip.conf sets `global.uploaded-prior-to = P3D`,
# which is not a valid ISO 8601 timestamp, so every pip invocation aborts with
# an isoformat error. Ignore pip config for this project rather than editing the
# user's global file. Reported to the user; remove this if they fix it.
export PIP_CONFIG_FILE=/dev/null
