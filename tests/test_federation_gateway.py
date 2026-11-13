"""Runs the human-gateway tests of YServerReddit (H5b) in a separate interpreter (same reason as test_federation_bus.py)."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_federation_gateway_in_isolation():
    env = dict(os.environ)
    sdk = ROOT.parent / "YFederation"
    if sdk.is_dir():
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(sdk), env.get("PYTHONPATH", "")]))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "tests" / "federation_gateway_impl.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1000:]
