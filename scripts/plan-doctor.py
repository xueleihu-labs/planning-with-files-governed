#!/usr/bin/env python3
# Version source: ../VERSION

"""Python wrapper for plan-doctor diagnostics.

Delegates to runtime.py doctor so CI can call a single cross-platform
Python entrypoint instead of platform-specific shell scripts.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import runtime  # noqa: E402


if __name__ == "__main__":
    runtime.main()
