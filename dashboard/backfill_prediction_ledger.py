#!/usr/bin/env python3
"""One-time (idempotent) bootstrap: seed the prediction ledger with a
publication event for every entry already present in the publication
registry. Safe to re-run -- already-seeded prop_ids are skipped."""
from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

try:
    from .publication_registry import DEFAULT_REGISTRY_PATH, load_registry
    from .prediction_ledger import (
        DEFAULT_LEDGER_PATH, backfill_from_registry, verify_ledger_integrity,
    )
except ImportError:
    from publication_registry import DEFAULT_REGISTRY_PATH, load_registry
    from prediction_ledger import (
        DEFAULT_LEDGER_PATH, backfill_from_registry, verify_ledger_integrity,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER_PATH)
    args = parser.parse_args()
    registry = load_registry(args.registry)
    added = backfill_from_registry(registry, path=args.ledger)
    summary = verify_ledger_integrity(args.ledger)
    print(
        f"Prediction ledger backfilled: {added} new event(s) added, "
        f"{summary['publication_count']} publication(s) on record, "
        f"integrity verified across {summary['event_count']} total event(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
