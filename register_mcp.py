# SPDX-License-Identifier: MIT
"""Register (or update) the stm32-probe MCP server in Claude Code's user config (~/.claude.json).

Usage:
    python register_mcp.py                          # register with this folder's .venv python
    python register_mcp.py --build-dir D:/proj/Debug
    python register_mcp.py -e STM32_SVD_DIR=D:/ST/CLT/STMicroelectronics_CMSIS_SVD
    python register_mcp.py --print                  # only print the JSON entry, do not write

The entry points at THIS folder, so run it from the copy you want Claude Code to use.
Env vars already set on an existing 'stm32-probe' entry are kept unless overridden.
A backup of ~/.claude.json is written next to it before any change.
"""
import argparse
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def entry(env):
    if os.name == "nt":
        py = os.path.join(HERE, ".venv", "Scripts", "python.exe")
    else:
        py = os.path.join(HERE, ".venv", "bin", "python")
    if not os.path.isfile(py):
        print(f"warning: {py} does not exist yet - run install.ps1 (or create .venv) first", file=sys.stderr)
    return {"type": "stdio", "command": py, "args": [os.path.join(HERE, "stm32_probe_mcp.py")], "env": env}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="stm32-probe")
    ap.add_argument("--build-dir", default=os.environ.get("STM32_BUILD_DIR", ""))
    ap.add_argument("-e", "--env", action="append", default=[], metavar="KEY=VALUE",
                    help="extra env var, e.g. STM32_CUBEIDE_ROOT=... (repeatable)")
    ap.add_argument("--print", action="store_true", help="print the entry only")
    a = ap.parse_args()

    cfg = os.path.join(os.path.expanduser("~"), ".claude.json")
    data = {}
    if os.path.isfile(cfg):
        with io.open(cfg, encoding="utf-8") as f:
            data = json.load(f)

    env = dict(data.get("mcpServers", {}).get(a.name, {}).get("env", {}))
    if a.build_dir:
        env["STM32_BUILD_DIR"] = a.build_dir
    for kv in a.env:
        k, sep, v = kv.partition("=")
        if not sep:
            ap.error(f"--env expects KEY=VALUE, got {kv!r}")
        env[k] = v
    e = entry(env)

    if a.print:
        print(json.dumps({a.name: e}, indent=2))
        return 0
    if os.path.isfile(cfg):
        shutil.copyfile(cfg, cfg + ".bak-stm32probe")
    data.setdefault("mcpServers", {})[a.name] = e
    with io.open(cfg, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"registered '{a.name}' in {cfg}")
    print(json.dumps(e, indent=2))
    print("Restart Claude Code to load the server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
