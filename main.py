#!/usr/bin/env python3

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BOT_FILE = BASE_DIR / "bot.py"

os.chdir(BASE_DIR)

print("[RUN] Railway starting bot.py...")

if not BOT_FILE.is_file():
    print("[ERROR] bot.py not found")
    sys.exit(1)

os.execv(
    sys.executable,
    [sys.executable, str(BOT_FILE)] + sys.argv[1:]
)
