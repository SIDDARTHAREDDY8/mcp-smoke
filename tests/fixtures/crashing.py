"""Crashing fixture: dies immediately (broken import, bad config, ...)."""

import sys

print(
    "Traceback: ModuleNotFoundError: No module named 'totally_real_dep'",
    file=sys.stderr,
)
sys.exit(1)
