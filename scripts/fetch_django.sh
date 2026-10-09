#!/usr/bin/env bash
# Fetch the pinned Django checkout used as the acceptance suite.
# The checkout is never committed; only the tag and SHA in django.lock are.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DJANGO_TAG="6.0.9"
DEST="$ROOT/.django"
LOCK="$ROOT/django.lock"

if [ -d "$DEST/.git" ]; then
  echo "checkout already present at $DEST"
  SHA="$(git -C "$DEST" rev-parse HEAD)"
else
  echo "cloning django $DJANGO_TAG into $DEST ..."
  git clone --depth 1 --branch "$DJANGO_TAG" \
    https://github.com/django/django.git "$DEST"
  SHA="$(git -C "$DEST" rev-parse HEAD)"
fi

cat > "$LOCK" <<EOF
tag=$DJANGO_TAG
sha=$SHA
EOF

echo "pinned: $DJANGO_TAG @ $SHA"
echo "wrote $LOCK"
