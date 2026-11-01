"""Runs the agent-action cross-post tests of YServerReddit (F6.2) in a separate interpreter.

Same reason and same way as test_federation_bus.py: the hook tests switch the application database and need tables the
shared test database does not have.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_federation_agent_crosspost_in_isolation():
    env = dict(os.environ)
    sdk = ROOT.parent / "YFederation"  # the hooks only need the SDK when a real gate is built; the tests use a stub
    if sdk.is_dir():
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(sdk), env.get("PYTHONPATH", "")]))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "tests" / "federation_agent_crosspost_impl.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1000:]
