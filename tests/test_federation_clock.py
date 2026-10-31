"""Runs the federated-clock hook tests of YServerReddit (F2.4) in a separate interpreter.

The hook tests need tables the shared test database does not have (db.create_all) and switch the
application database; running them in-process would pollute the other tests of this repository.
The subprocess gets its own throw-away database from tests/conftest.py.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_federation_clock_hooks_in_isolation():
    env = dict(os.environ)
    sdk = ROOT.parent / "YFederation"  # the hooks only need the SDK when a real gate is built; the tests use a stub
    if sdk.is_dir():
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(sdk), env.get("PYTHONPATH", "")]))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "tests" / "federation_clock_impl.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1000:]
