#!/usr/bin/env python3
"""Fail closed on corrupt or internally inconsistent publication state."""
from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    from .publication_registry import DEFAULT_REGISTRY_PATH, load_registry, validate_registry
except ImportError:
    from publication_registry import DEFAULT_REGISTRY_PATH, load_registry, validate_registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default=DEFAULT_REGISTRY_PATH)
    args = parser.parse_args()
    if not os.path.exists(args.path):
        print("Publication registry has not been initialized; rollout verifier accepts missing state.")
        return 0
    try:
        registry = load_registry(args.path)
        validate_registry(registry)
    except (RuntimeError, ValueError) as exc:
        print(f"Publication registry verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Publication registry verified: {len(registry['entries'])} exposure(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
