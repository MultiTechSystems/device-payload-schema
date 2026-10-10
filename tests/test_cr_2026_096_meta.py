"""_meta in the reference interpreter: CR-2026-096 (PS-490 to PS-497), with PS-175 to
PS-181, PS-340 to PS-342, PS-371 to PS-376, CR-2026-088's PS-480/481 and CR-2026-095's
PS-489.

The fixtures `_language-conformance/meta-*.yaml`, and `expected_meta` on name-from,
name-from-var and repeat-identity, hold every interpreter to the output. What a fixture
cannot state is here: a rejected schema, a failed decode, a bad input context, and that
`decode()` is unchanged for its callers.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import (  # noqa: E402
    SchemaInterpreter,
    meta_declaration_errors,
    normalise_dev_eui,
    rx_time_seconds,
)
from validate_schema import meta_matches  # noqa: E402

FLAT = {
    "name": "flat",
    "fields": [
        {"name": "a", "type": "u8", "unit": "V"},
        {"name": "b", "type": "u8"},
    ],
}


def test_decode_is_unchanged():
    # td-tools reads decode(); _meta comes only from interpret() (PS-467's split).
    interpreter = SchemaInterpreter(FLAT)
    assert interpreter.decode(b"\x01\x02").data == {"a": 1, "b": 2}
    assert "_meta" in interpreter.interpret(b"\x01\x02").data


def test_meta_is_last_and_present_without_annotations():
    # PS-180: produced with every decoded result, whether or not the schema annotates.
    data = SchemaInterpreter(FLAT).interpret(b"\x01\x02").data
    assert list(data)[-1] == "_meta"
    assert data["_meta"] == {
        "schema": "flat",
        "fields": {"a": {"type": "u8", "unit": "V"}, "b": {"type": "u8"}},
    }


def test_a_failed_decode_has_no_meta():
    # PS-497.
    result = SchemaInterpreter(FLAT).interpret(b"\x01")
    assert not result.success and "_meta" not in result.data


def test_fport_comes_from_the_argument_or_the_context():
    interpreter = SchemaInterpreter(FLAT)
    assert interpreter.interpret(b"\x01\x02", fPort=5).data["_meta"]["fPort"] == 5
    assert (
        interpreter.interpret(b"\x01\x02", input_metadata={"fPort": 6}).data["_meta"][
            "fPort"
        ]
        == 6
    )
    assert "fPort" not in interpreter.interpret(b"\x01\x02").data["_meta"]  # PS-341


@pytest.mark.parametrize(
    "text,eui",
    [
        ("0011223344556677", "0011223344556677"),
        ("00-11-22-33-44-55-66-AA", "00112233445566aa"),
        ("00:11:22:33:44:55:66:AA", "00112233445566aa"),
        ("00 11 22 33 44 55 66 aa", "00112233445566aa"),
        ("0011", None),
        ("00112233445566GG", None),
        (1234, None),
    ],
)
def test_dev_eui_is_normalised(text, eui):
    assert normalise_dev_eui(text) == eui  # PS-496


@pytest.mark.parametrize(
    "context,message",
    [
        (
            {"devEUI": "nonsense"},
            "devEUI 'nonsense' is not 16 hexadecimal digits (PS-496)",
        ),
        (
            {"recvTime": "yesterday"},
            "recvTime 'yesterday' is not an ISO 8601 time or a number of seconds (PS-495)",
        ),
    ],
)
def test_a_malformed_context_fails_with_no_meta(context, message):
    result = SchemaInterpreter(FLAT).interpret(b"\x01\x02", input_metadata=context)
    assert not result.success and "_meta" not in result.data
    assert result.errors == [message]


@pytest.mark.parametrize(
    "recv,seconds",
    [
        ("2026-08-26T12:00:00Z", 1787745600),
        ("2026-08-26T12:00:00.123Z", 1787745600.123),
        ("2026-08-26T12:00:00.120000Z", 1787745600.12),
        ("2026-08-26T12:00:00.1234Z", 1787745600.123),
        ("2026-08-26T12:00:00.1235Z", 1787745600.124),
        ("2026-08-26T12:00:00.1225Z", 1787745600.122),
        ("2026-08-26T12:00:00.9996Z", 1787745601),
        ("2026-08-26T12:00:00.000Z", 1787745600),
        ("2026-08-26T14:00:00+02:00", 1787745600),
        ("2026-08-26T07:30:00-0430", 1787745600),
        ("2026-08-26T12:00:00", 1787745600),
        (1787745600.5, 1787745600.5),
        ("yesterday", None),
        (True, None),
    ],
)
def test_recv_time_is_unix_seconds(recv, seconds):
    got = rx_time_seconds(recv)
    assert got == seconds and type(got) is type(seconds)  # PS-177, PS-495


def test_declarations_that_disagree_on_meaning_are_rejected():
    # PS-491: unit, senml and ipso must agree; type and description may differ.
    def schema(second):
        return {
            "name": "x",
            "fields": [
                {"name": "_k", "type": "u8"},
                {
                    "match": {
                        "field": "$_k",
                        "cases": {
                            1: [{"name": "r", "type": "u8", "unit": "Cel"}],
                            2: [dict({"name": "r"}, **second)],
                        },
                    }
                },
            ],
        }

    assert meta_declaration_errors(schema({"type": "s16", "unit": "Cel"})) == []
    assert (
        meta_declaration_errors(
            schema({"type": "u8", "unit": "Cel", "description": "other"})
        )
        == []
    )
    for second in (
        {"type": "u8", "unit": "K"},
        {"type": "u8", "unit": "Cel", "senml": {"name": "t"}},
        {"type": "u8", "unit": "Cel", "ipso": {"object": 3303}},
    ):
        errors = meta_declaration_errors(schema(second))
        assert len(errors) == 1 and "PS-491" in errors[0], second
        result = SchemaInterpreter(schema(second)).decode(b"\x01\x05")
        assert not result.success and any("PS-491" in e for e in result.errors)


def test_names_on_different_ports_need_not_agree():
    # PS-491's scope is one field list; oyster's latitude is number on one port and s32
    # on another, and _meta's type is per port.
    schema = {
        "name": "x",
        "ports": {
            1: {"fields": [{"name": "r", "type": "u8", "unit": "Cel"}]},
            2: {"fields": [{"name": "r", "type": "u8", "unit": "K"}]},
        },
    }
    assert meta_declaration_errors(schema) == []


def test_the_corpus_breaks_no_agreement_rule():
    # Measured before CR-2026-096: no corpus schema declares one name with different
    # unit, senml or ipso in one field list.
    for path in sorted((REPO_ROOT / "schemas").rglob("*.yaml")):
        document = yaml.safe_load(path.read_text())
        if isinstance(document, dict):
            assert meta_declaration_errors(document) == [], path


def test_every_reported_key_has_exactly_one_entry_across_the_corpus():
    # PS-181 as amended: exactly one entry per top-level reported key, and no other.
    checked = 0
    for path in sorted((REPO_ROOT / "schemas" / "devices").rglob("*.yaml")):
        document = yaml.safe_load(path.read_text())
        if not isinstance(document, dict):
            continue
        interpreter = SchemaInterpreter(document)
        for vector in document.get("test_vectors") or []:
            if "payload" not in vector:
                continue
            result = interpreter.interpret(
                bytes.fromhex(str(vector["payload"]).replace(" ", "")),
                fPort=vector.get("fPort", vector.get("fport")),
                input_metadata=vector.get("input_metadata"),
            )
            if not result.success:
                continue
            reported = {k for k in result.data if not k.startswith("_")}
            assert set(result.data["_meta"]["fields"]) == reported, path
            checked += 1
    assert checked > 2000


def test_meta_matches_is_exact():
    assert meta_matches({"a": 1}, {"a": 1.0}) == (True, "")
    assert not meta_matches({"a": 1}, {"a": 1, "b": 2})[0]
    assert not meta_matches({"a": 1, "b": 2}, {"a": 1})[0]
    assert not meta_matches({"a": True}, {"a": 1})[0]
    assert not meta_matches({"a": [1, 2]}, {"a": [2, 1]})[0]
    ok, detail = meta_matches(
        {"f": {"x": {"type": "u8"}}}, {"f": {"x": {"type": "s8"}}}
    )
    assert not ok and "_meta.f.x.type" in detail


def test_a_wrong_expectation_fails_the_fixture():
    # The runner's comparison is not vacuous: the fixtures' expectations, perturbed, fail.
    path = REPO_ROOT / "schemas/devices/_language-conformance/meta-nested.yaml"
    document = yaml.safe_load(path.read_text())
    vector = document["test_vectors"][0]
    actual = (
        SchemaInterpreter(document)
        .interpret(bytes.fromhex(vector["payload"].replace(" ", "")))
        .data["_meta"]
    )
    assert meta_matches(vector["expected_meta"], actual)[0]
    wrong = yaml.safe_load(yaml.safe_dump(vector["expected_meta"]))
    del wrong["fields"]["channels"]["identity"]["open"]
    assert not meta_matches(wrong, actual)[0]


def test_the_generated_codec_falls_through_to_a_default_port():
    # PS-024. The generator refused a `default` port entry (int('default')), which no
    # corpus schema had until meta-port-default.yaml; the fixture needs all five paths.
    import json
    import subprocess

    from generate_ts013_codec import TS013Generator

    path = REPO_ROOT / "schemas/devices/_language-conformance/meta-port-default.yaml"
    code = TS013Generator(yaml.safe_load(path.read_text())).generate()
    call = (
        "var u = decodeUplink({bytes: [90], fPort: 9});"
        "var e = encodeDownlink({data: u.data, fPort: 9});"
        "process.stdout.write(JSON.stringify([u, e]));"
    )
    out = subprocess.run(
        ["node"], input=code + "\n" + call, capture_output=True, text=True, check=True
    )
    decoded, encoded = json.loads(out.stdout)
    assert decoded == {"data": {"level": 90}, "warnings": [], "errors": []}
    assert encoded["bytes"] == [90] and encoded["fPort"] == 9 and not encoded["errors"]


def _verdicts_gate():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verdicts_gate", str(REPO_ROOT / "tools" / "verdicts-gate.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_gate_compares_meta_exactly():
    gate = _verdicts_gate()
    key = ("a.yaml", 0)
    reference = {key: {"schema": "a", "fields": {"x": {"type": "u8"}}}}
    assert gate.meta_differences(reference, dict(reference)) == []
    wrong = {key: {"schema": "a", "fields": {"x": {"type": "s8"}}}}
    assert gate.meta_differences(reference, wrong)
    assert gate.meta_differences(reference, {key: None})  # one failed
    assert gate.meta_differences(reference, {})  # not reported
    assert gate.meta_differences({key: None}, {key: None}) == []  # both failed


def test_the_gate_sets_aside_only_metadata_block_keys():
    # PS-310's enrichment is Python's alone: an empty entry the other lacks is set aside,
    # but a real entry the other lacks is still a difference.
    gate = _verdicts_gate()
    key = ("a.yaml", 0)
    other = {key: {"schema": "a", "fields": {"x": {"type": "u8"}}}}
    enriched = {
        key: {"schema": "a", "fields": {"x": {"type": "u8"}, "received_at": {}}}
    }
    assert gate.meta_differences(enriched, other) == []
    extra = {key: {"schema": "a", "fields": {"x": {"type": "u8"}, "y": {"type": "u8"}}}}
    assert gate.meta_differences(extra, other)


def test_the_runners_are_asked_for_a_meta_report():
    gate = _verdicts_gate()
    command = " ".join(
        gate.docker_command("go", "/work/build/verdicts/go.json", None, None)
    )
    assert "CORPUS_META_REPORT=/work/build/verdicts/go.meta.json" in command
