#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys


def main() -> None:
    print(f"Python: {sys.version.split()[0]}")
    for package in ("openai", "dotenv", "pydantic", "streamlit"):
        status = "ok" if importlib.util.find_spec(package) else "missing"
        print(f"{package}: {status}")
    print(f"OPENAI_API_KEY: {'set' if os.getenv('OPENAI_API_KEY') else 'not set'}")
    print(f"OPENAI_MODEL: {os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')}")


if __name__ == "__main__":
    main()
