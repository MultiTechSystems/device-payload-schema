"""The documented `schema_interpreter.py decode` command decodes.

Five documents showed `python3 tools/schema_interpreter.py decode <schema> <hex>` while
the script ran a hardcoded demo whatever it was given, so every documented invocation
printed the demo's output. The demo is kept for a bare invocation.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "schema_interpreter.py"
SCHEMA = REPO_ROOT / "schemas" / "devices" / "decentlab" / "dl-5tm.yaml"


def run(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args], capture_output=True, text=True, timeout=60
    )


def test_decode_prints_the_result_as_json():
    done = run("decode", str(SCHEMA), "02 1234 0003 01F4 0190 0C1C")
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["success"] is True
    assert out["data"]["device_id"] == 4660
    assert out["errors"] == []


def test_a_failed_decode_exits_non_zero_and_says_why():
    done = run("decode", str(SCHEMA), "02")
    assert done.returncode == 1
    out = json.loads(done.stdout)
    assert out["success"] is False and out["errors"]


def test_fport_is_passed_through():
    done = run("decode", str(SCHEMA), "02 1234 0003 01F4 0190 0C1C", "--fport", "1")
    assert done.returncode == 0, done.stderr


def test_a_bare_invocation_still_runs_the_demo():
    done = run()
    assert done.returncode == 0, done.stderr
    assert "Schema Interpreter Demo" in done.stdout
