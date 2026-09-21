"""Hanging fixture: accepts stdin but never responds (deadlock, blocking init)."""

import time

while True:
    time.sleep(3600)
