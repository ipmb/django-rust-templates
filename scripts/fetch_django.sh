#!/usr/bin/env bash
# Fetch the pinned Django checkout used as the acceptance suite.
# The checkout is never committed; only the tag and SHA in django.lock are.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/.django"
LOCK="$ROOT/django.lock"
DJANGO_TAG="$(sed -n 's/^tag=//p' "$LOCK")"
EXPECTED_SHA="$(sed -n 's/^sha=//p' "$LOCK")"
if [[ ! "$DJANGO_TAG" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ ! "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "invalid Django tag or SHA in $LOCK" >&2
  exit 1
fi

if [ -d "$DEST/.git" ]; then
  echo "checkout already present at $DEST"
  SHA="$(git -C "$DEST" rev-parse HEAD)"
else
  echo "cloning django $DJANGO_TAG into $DEST ..."
  git clone --depth 1 --branch "$DJANGO_TAG" \
    https://github.com/django/django.git "$DEST"
  SHA="$(git -C "$DEST" rev-parse HEAD)"
fi

if [ "$SHA" != "$EXPECTED_SHA" ]; then
  echo "Django checkout is $SHA; expected $EXPECTED_SHA from $LOCK" >&2
  exit 1
fi

echo "pinned: $DJANGO_TAG @ $SHA"
python3 "$ROOT/scripts/assert_checkout_pinned.py"
