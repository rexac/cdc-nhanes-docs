#!/usr/bin/env python3
"""Bootstrap/repair a CDC NHANES codebook mirror.

This command intentionally reuses the canonical sync implementation instead of
maintaining a second downloader with its own cycle/component rules.
"""

from __future__ import annotations

import argparse
import sys

from nhanes_core import DEFAULT_ROOT
from sync_cdc_docs import CDCSync


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download all missing CDC NHANES public codebook HTML files"
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=DEFAULT_ROOT,
        help="mirror root directory (default: NHANES_DOCS or /HostData/Data/cdc_docs)",
    )
    parser.add_argument("--quiet", action="store_true", help="reduce log output")
    parser.add_argument(
        "--include-non-public",
        action="store_true",
        help="also download publicly viewable RDC/limited-access codebooks",
    )
    parser.add_argument(
        "--verify-content",
        action="store_true",
        help="also compare and update every existing codebook (slower)",
    )
    args = parser.parse_args()

    sync = CDCSync(
        args.target,
        check_only=False,
        prune=False,
        quiet=args.quiet,
        verify_content=args.verify_content,
        include_non_public=args.include_non_public,
    )
    try:
        code = sync.run()
    finally:
        sync.client.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
