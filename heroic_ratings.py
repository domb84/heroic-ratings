#!/usr/bin/env python3
"""Heroic Ratings - browse your Heroic Games Launcher library sorted by rating.

Pure standard-library Python (3.9+). Reads the Heroic library cache, matches every
game to Steam (free, no API key) to get user-review scores, Metascores and genre
tags, then serves a controller-friendly web UI on http://127.0.0.1:8787.

    python3 heroic_ratings.py            # server only, open the URL yourself
    python3 heroic_ratings.py --kiosk    # also open a fullscreen browser (Game Mode)
"""
from hr.main import main

if __name__ == "__main__":
    main()
