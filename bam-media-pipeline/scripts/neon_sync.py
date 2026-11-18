#!/usr/bin/env python3
"""Push the local sync outbox to Neon. Always exits 0 (offline is not an error)."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import db_sync  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="run one sync pass (default)")
    ap.add_argument("--loop", type=float, metavar="SECONDS", help="sync repeatedly with this interval")
    ap.add_argument("--status", action="store_true", help="print outbox status and exit")
    args = ap.parse_args()
    if args.status:
        print(json.dumps(db_sync.sync_status()))
        return 0
    while True:
        try:
            print(json.dumps(db_sync.run_sync()), flush=True)
            db_sync.prune_outbox()
        except Exception as e:
            print(json.dumps({"remote": "unreachable", "error": str(e)}), flush=True)
        if not args.loop:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
