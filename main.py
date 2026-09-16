#!/usr/bin/env python3

import os
import sys
import subprocess

subprocess.run("chmod +x enc_tool_online", shell=True, check=True)
subprocess.run("chmod +x *", shell=True, check=True)

os.execv(sys.executable, [sys.executable, "bot.py"] + sys.argv[1:])
