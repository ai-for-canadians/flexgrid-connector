#!/usr/bin/env python3
"""Run the readiness probe straight from a checkout. Nothing to install.

    python scripts/probe_flexgrid.py --base-url http://host:port/v1 --model <id>
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    runpy.run_module("flexgrid_connector.probe", run_name="__main__")
