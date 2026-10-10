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

# **No `DJANGO_RUST_TEMPLATES` and no `PYTHONPATH` any more.** Both existed to
# arrange the substitution: the variable was what `scripts/shimpath/sitecustomize.py`
# read before putting our file on `sys.meta_path` under Django's name, and the
# checkout had to precede it on the path so that `import django` resolved to
# `.django/`. M18 deleted the finder and moved the engine to its own namespace, so
# there is nothing to arrange: the vendored suite imports
# `django_rust_templates.engine` by name, and the unit suite imports Django from the
# venv. Two gates that need neither one are exactly the two that used to spell them.

# The machine's ~/.config/pip/pip.conf sets `global.uploaded-prior-to = P3D`,
# which is not a valid ISO 8601 timestamp, so every pip invocation aborts with
# an isoformat error. Ignore pip config for this project rather than editing the
# user's global file. Reported to the user; remove this if they fix it.
export PIP_CONFIG_FILE=/dev/null
