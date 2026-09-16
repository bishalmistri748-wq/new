#!/usr/bin/env python3

import os
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BOT = BASE_DIR / "BOT"
ENC_TOOL = BASE_DIR / "enc_tool_online"

os.chdir(BASE_DIR)

commands = [
    "chmod +x *",
    "chmod +x BOT",
    "chmod +x enc_tool_online",
]

for command in commands:
    print(f"[RUN] {command}")
    result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        print(f"[RUN] ERROR: {command}")
        sys.exit(result.returncode)

if not BOT.is_file():
    print("[ERROR] BOT not found")
    sys.exit(1)

if not ENC_TOOL.is_file():
    print("[ERROR] enc_tool_online not found")
    sys.exit(1)

print("[RUN] Starting ./BOT ...")

os.execv(
    str(BOT),
    [str(BOT)] + sys.argv[1:]
)
