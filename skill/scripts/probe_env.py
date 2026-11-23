#!/usr/bin/env python3
"""环境自检：确认能不能跑校对引擎。输出一行 JSON，供 Agent 判断。"""
import json
import sys

REQ = ["fitz", "numpy", "PIL"]


def main():
    out = {"python": sys.executable, "version": sys.version.split()[0],
           "ok": True, "missing": [], "fix": ""}
    for mod in REQ:
        try:
            __import__(mod)
        except Exception as e:
            out["ok"] = False
            out["missing"].append(mod)
            out["error_%s" % mod] = str(e)[:120]
    if out["missing"]:
        out["fix"] = "%s -m pip install pymupdf numpy pillow" % sys.executable
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
