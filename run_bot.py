#!/usr/bin/env python3
import os
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BOT = BASE_DIR / "BOT"
ENC_TOOL = BASE_DIR / "enc_tool_online"

os.chdir(BASE_DIR)

# Pehle user ke requested 3 commands EXACT order mein run honge.
commands = [
    "chmod +x *",
    "chmod +x BOT",
    "chmod +x enc_tool_online",
]

for command in commands:
    print(f"[RUN] {command}")
    result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        print(f"[RUN] ERROR: command failed: {command}")
        sys.exit(result.returncode)

# 3 commands successful hone ke baad hi ./BOT run hoga.
if not BOT.is_file():
    print("[RUN] ERROR: BOT not found.")
    sys.exit(1)

if not ENC_TOOL.is_file():
    print("[RUN] ERROR: enc_tool_online not found.")
    sys.exit(1)

# Sirf ./BOT ko run karo.
os.execv("./BOT", ["./BOT"] + sys.argv[1:])
