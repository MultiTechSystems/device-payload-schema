"""Mutation coverage for the CR-2026-010 direction check.

The requirement it implements went unimplemented in six languages and cited-but-untested
in two suites, so "the tests pass" is weak evidence here. This module asks the stronger
question: if the check were weakened in each of the ways it could plausibly be weakened,
would `test_cr_2026_010_direction.py` notice?

Each mutant below is one such weakening - the check disabled, a declaration ignored, the
error demoted to a warning, the withdrawn spelling quietly accepted, the pre-CR generator
default restored. The test asserts the suite fails on every one. A mutant that survives
means a decision the CR argued for is not actually pinned by a test.

Mutants run against a copy of the two tools in a temporary tree, never against the
working source, so a failure here cannot leave a mutated interpreter behind. The control
case asserts the unmutated copy passes, which is what makes a mutant's failure
attributable to the mutation rather than to the harness.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INTERPRETER = "tools/schema_interpreter.py"
GENERATOR = "tools/generate_ts013_codec.py"
SUITE = "tests/test_cr_2026_010_direction.py"

# Mutants that do not touch the generator are checked against the interpreter tests
# alone, so only the generator mutants pay for spawning node.
INTERPRETER_TESTS = "not TestGeneratedCodec"
GENERATOR_TESTS = "TestGeneratedCodec"

#: (name, file, old source, replacement, which tests must notice)
MUTANTS = [
    (
        "check disabled outright",
        INTERPRETER,
        "        if direction is None:\n            return None",
        "        if True:\n            return None",
        INTERPRETER_TESTS,
    ),
    (
        "mismatch accepted instead of reported",
        INTERPRETER,
        "        return f'{label} is declared direction:{declared}; message direction is {direction}'",
        "        return None",
        INTERPRETER_TESTS,
    ),
    (
        "uplink-declared entries stop being checked",
        INTERPRETER,
        "        if declared in ('both', direction):",
        "        if declared in ('both', direction, 'uplink'):",
        INTERPRETER_TESTS,
    ),
    (
        "port-level declaration ignored, only schema-level read",
        INTERPRETER,
        "        declared = self.schema.get('direction') if entry is None else entry.get('direction')",
        "        declared = self.schema.get('direction')",
        INTERPRETER_TESTS,
    ),
    (
        "withdrawn `bidirectional` spelling accepted",
        INTERPRETER,
        "DECLARED_DIRECTIONS = frozenset({'uplink', 'downlink', 'both'})",
        "DECLARED_DIRECTIONS = frozenset({'uplink', 'downlink', 'both', 'bidirectional'})",
        INTERPRETER_TESTS,
    ),
    (
        "unknown declared value treated as valid",
        INTERPRETER,
        "        if declared not in DECLARED_DIRECTIONS:",
        "        if declared in DECLARED_DIRECTIONS and False:",
        INTERPRETER_TESTS,
    ),
    (
        "the default entry named as the FPort that missed",
        INTERPRETER,
        "            return ports['default'], 'the default port entry'",
        "            return ports['default'], f'fPort {fPort}'",
        INTERPRETER_TESTS,
    ),
    (
        "decode demotes the error to a warning",
        INTERPRETER,
        "        direction_error = self._direction_error(fPort, direction)\n"
        "        if direction_error:\n"
        "            result.errors.append(direction_error)\n"
        "            return result\n\n"
        "        # Track current data for match references",
        "        direction_error = self._direction_error(fPort, direction)\n"
        "        if direction_error:\n"
        "            result.warnings.append(direction_error)\n\n"
        "        # Track current data for match references",
        INTERPRETER_TESTS,
    ),
    (
        "decode reports the error but decodes anyway",
        INTERPRETER,
        "            result.errors.append(direction_error)\n"
        "            return result\n\n"
        "        # Track current data for match references",
        "            result.errors.append(direction_error)\n\n"
        "        # Track current data for match references",
        INTERPRETER_TESTS,
    ),
    (
        "encode produces the payload anyway",
        INTERPRETER,
        "            result.errors.append(direction_error)\n"
        "            return result\n\n"
        "        output = bytearray()",
        "            result.errors.append(direction_error)\n\n"
        "        output = bytearray()",
        INTERPRETER_TESTS,
    ),
    (
        "generator restores the pre-CR uplink default",
        GENERATOR,
        "DEFAULT_PORT_DIRECTION = 'both'",
        "DEFAULT_PORT_DIRECTION = 'uplink'",
        GENERATOR_TESTS,
    ),
    (
        "generator reports the wrong direction as an unknown FPort",
        GENERATOR,
        "                        lines.append(f'      return {{ data: {{}}, warnings: [], errors: [\"{message}\"] }};')",
        "                        lines.append('      return { data: {}, warnings: [\"Unknown fPort: \" + input.fPort], errors: [] };')",
        GENERATOR_TESTS,
    ),
    (
        "generator refuses the direction it should accept",
        GENERATOR,
        "                    if declared in (direction, 'both'):",
        "                    if declared == direction:",
        GENERATOR_TESTS,
    ),
]


@pytest.fixture(scope="module")
def mutant_tree(tmp_path_factory):
    """A minimal copy of the tree the suite needs: the two tools and the suite itself."""
    root = tmp_path_factory.mktemp("cr010_mutants")
    (root / "tools").mkdir()
    (root / "tests").mkdir()
    for rel in (INTERPRETER, GENERATOR, SUITE):
        shutil.copy2(REPO_ROOT / rel, root / rel)
    return root


def run_suite(root, selection):
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(root / SUITE),
         "-q", "-x", "-p", "no:cacheprovider", "-c", str(root / "pytest.ini"),
         "-k", selection],
        capture_output=True, text=True, timeout=300, cwd=root,
    )


@pytest.fixture(scope="module", autouse=True)
def _empty_config(mutant_tree):
    # An empty config keeps the repo's addopts and testpaths out of the mutant runs.
    (mutant_tree / "pytest.ini").write_text("[pytest]\n")


def test_the_unmutated_copy_passes(mutant_tree):
    """Control: without it, a mutant's failure could be an artefact of the copy."""
    for selection in (INTERPRETER_TESTS, GENERATOR_TESTS):
        result = run_suite(mutant_tree, selection)
        assert result.returncode == 0, f"{selection}\n{result.stdout[-3000:]}"


@pytest.mark.parametrize(
    "name,rel,old,new,selection", MUTANTS, ids=[m[0] for m in MUTANTS]
)
def test_the_suite_kills_the_mutant(mutant_tree, name, rel, old, new, selection):
    target = mutant_tree / rel
    original = target.read_text()
    assert original.count(old) == 1, (
        f"mutant {name!r} no longer applies: its target text appears "
        f"{original.count(old)} times in {rel}. Update the mutant with the code."
    )
    try:
        target.write_text(original.replace(old, new, 1))
        result = run_suite(mutant_tree, selection)
        assert result.returncode != 0, (
            f"mutant survived: {name}. The behaviour it removes is not pinned by any "
            f"test, so the suite would not notice this regression.\n"
            f"{result.stdout[-3000:]}"
        )
    finally:
        target.write_text(original)
