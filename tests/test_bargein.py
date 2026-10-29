"""Runs the browser barge-in detector's checks (tests/bargein.test.cjs) under Node."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_bargein_detector():
    script = Path(__file__).with_name("bargein.test.cjs")
    out = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr or out.stdout
