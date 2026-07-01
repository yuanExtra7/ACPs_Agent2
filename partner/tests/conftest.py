from __future__ import annotations

import sys
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

ACPS_SDK_DIR = Path(__file__).resolve().parents[2] / "ACPs-community" / "acps-sdk"
if str(ACPS_SDK_DIR) not in sys.path:
    sys.path.insert(0, str(ACPS_SDK_DIR))

