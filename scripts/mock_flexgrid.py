#!/usr/bin/env python3
"""Run the mock FlexGrid server straight from a checkout. Nothing to install.

    python scripts/mock_flexgrid.py --port 8089 --tps 105
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    runpy.run_module("flexgrid_connector.mock_server", run_name="__main__")
