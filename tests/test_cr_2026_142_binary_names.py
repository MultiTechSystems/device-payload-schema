"""CR-2026-142 (PS-555): the binary schema form carries every field's name.

`tools/schema_binary.py` wrote each field as a type byte, a scale and a 2-byte IPSO object
and no name, and `schema_load_binary` in include/schema_interpreter.h made one up from the
object: 3303 became "temperature", 3316 "voltage", anything else "field_xxxx". A schema
decoded from its binary form therefore reported different keys from the same schema in
YAML. PS-555 requires every serialized form to carry the names unchanged, and forbids
deriving a name from the IPSO object, the type or the position.

Format version 3:

- byte 2 is the format version (3), and a reader refuses any other. Version 1 put the
  schema's own `version` there; 2 is binary_schema_v2.py's different layout under the same
  magic, so it is skipped rather than reused;
- the count byte's bit 7 says a name table follows the records, and bits 0-6 hold the field
  count, at most 127;
- the name table is one length byte and that many bytes of UTF-8 per record, in field
  order, length 0 only for a `skip`;
- `skip` has the type code 0x7, so a skip has a record at all (0x8 collided with the lookup
  flag and was refused).

A blob may leave the name table out for an interpreter that hands back values by index.
Such a form cannot report the decoded object (PS-555), and the C loader then reports no
name at all.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
HARNESS = TOOLS / "c-corpus-harness.py"
sys.path.insert(0, str(TOOLS))

import binary_schema_loader  # noqa: E402
import schema_binary as sb  # noqa: E402


def _schema(*fields, **top):
    return dict({"name": "probe", "endian": "big", "fields": list(fields)}, **top)


def _serializable():
    """Every corpus and example schema the binary form carries."""
    found = []
    for path in sorted(
        list((REPO_ROOT / "schemas" / "devices").rglob("*.yaml"))
        + list((REPO_ROOT / "examples").glob("*.yaml"))
    ):
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("fields"), list):
            continue
        try:
            sb.encode_schema(doc)
        except Exception:
            continue
        found.append(path)
    return found


SERIALIZABLE = _serializable()


def test_the_corpus_has_schemas_the_form_carries():
    # 11 corpus, 2 examples and the 48 composed library vectors before CR-2026-142; the
    # three skip fixtures join them now that a skip has a record.
    assert len(SERIALIZABLE) >= 64, len(SERIALIZABLE)


@pytest.mark.parametrize(
    "path", SERIALIZABLE, ids=[str(p.relative_to(REPO_ROOT)) for p in SERIALIZABLE]
)
def test_names_round_trip_in_both_forms(path):
    doc = yaml.safe_load(path.read_text())
    names = [f.get("name") or None for f in doc["fields"]]

    named = sb.decode_schema(sb.encode_schema(doc))
    assert named["names"] is True
    assert [f.get("name") for f in named["fields"]] == names

    bare_blob = sb.encode_schema(doc, names=False)
    bare = sb.decode_schema(bare_blob)
    assert bare["names"] is False
    assert all("name" not in f for f in bare["fields"])
    assert [f["type"] for f in bare["fields"]] == [f["type"] for f in named["fields"]]
    # The records are the same bytes; only the header bit and the table differ.
    assert sb.encode_schema(doc)[5 : len(bare_blob)] == bare_blob[5:]


class TestTheLayout:
    def test_the_header_carries_the_format_version_and_the_names_flag(self):
        blob = sb.encode_schema(
            _schema({"name": "t", "type": "u8"}, {"name": "h", "type": "u8"}),
            names=True,
        )
        assert blob[:5] == bytes([0x50, 0x53, 3, 0x00, 0x82])
        assert sb.encode_schema(_schema({"name": "t", "type": "u8"}), names=False)[
            :5
        ] == bytes([0x50, 0x53, 3, 0x00, 0x01])

    def test_the_name_table_follows_the_records(self):
        """The companion document's worked example, less its magic and version."""
        doc = yaml.safe_load((REPO_ROOT / "examples" / "env_sensor.yaml").read_text())
        blob = sb.encode_schema(doc)
        assert blob[4] == 0x84
        assert blob[21:] == (
            b"\x0btemperature" b"\x08humidity" b"\x0abattery_mv" b"\x06status"
        )
        assert len(blob) == 5 + 4 * 4 + 4 + 35

    def test_the_schemas_own_version_is_not_the_format_version(self):
        blob = sb.encode_schema(_schema({"name": "t", "type": "u8"}, version=7))
        assert blob[2] == sb.FORMAT_VERSION == 3

    def test_a_skip_has_a_record_and_an_empty_name(self):
        doc = _schema(
            {"name": "a", "type": "u8"},
            {"type": "skip", "length": 2},
            {"name": "b", "type": "u8"},
        )
        blob = sb.encode_schema(doc)
        assert blob[9] == 0x72  # type 7, two bytes
        assert blob.endswith(b"\x01a" b"\x00" b"\x01b")
        back = sb.decode_schema(blob)
        assert back["fields"][1] == {"type": "skip", "length": 2}

    def test_a_named_skip_keeps_its_name(self):
        """PS-555 carries every name unchanged, a skip's included where it has one."""
        doc = _schema({"name": "pad", "type": "skip", "length": 1})
        assert sb.decode_schema(sb.encode_schema(doc))["fields"][0]["name"] == "pad"

    def test_a_name_is_utf8_and_unrestricted(self):
        name = "CO₂ [ppm]"
        back = sb.decode_schema(
            sb.encode_schema(_schema({"name": name, "type": "u16"}))
        )
        assert back["fields"][0]["name"] == name

    def test_the_record_id_is_the_ipso_object_or_zero(self):
        """Not a hash of the name, which Python salts per process, and not an object
        guessed from the name."""
        blob = sb.encode_schema(
            _schema(
                {"name": "temperature", "type": "u8"},
                {"name": "t", "type": "u8", "ipso": {"object": 3303}},
            ),
            names=False,
        )
        assert blob[7:9] == b"\x00\x00"
        assert blob[11:13] == (3303).to_bytes(2, "little")

    def test_the_bytes_do_not_vary_between_processes(self, tmp_path):
        path = tmp_path / "s.yaml"
        path.write_text(
            yaml.safe_dump(_schema({"name": "custom_reading", "type": "u16"}))
        )
        outputs = set()
        for seed in ("1", "2"):
            done = subprocess.run(
                [sys.executable, str(TOOLS / "schema_binary.py"), "encode", str(path)],
                capture_output=True,
                text=True,
                env=dict(os.environ, PYTHONHASHSEED=seed),
                check=True,
            )
            outputs.add(done.stdout.strip())
        assert len(outputs) == 1


class TestWhatIsRefused:
    def test_a_name_longer_than_the_c_interpreter_holds(self):
        name = "n" * (sb.C_MAX_NAME_BYTES + 1)
        errors = sb.schema_errors(_schema({"name": name, "type": "u8"}))
        assert any(
            f"field {name!r}: a 32-byte name" in e
            and "never truncated" in e
            and "PS-555" in e
            for e in errors
        ), errors
        assert not sb.schema_errors(
            _schema({"name": "n" * sb.C_MAX_NAME_BYTES, "type": "u8"})
        )

    def test_a_field_with_no_name(self):
        errors = sb.schema_errors(_schema({"type": "u8"}))
        assert any("no `name`" in e and "PS-553" in e for e in errors), errors

    def test_more_fields_than_the_count_holds(self):
        fields = [{"name": f"f{i}", "type": "u8"} for i in range(128)]
        with pytest.raises(sb.UnrepresentableSchemaError) as caught:
            sb.encode_schema(_schema(*fields))
        assert "at most 127" in str(caught.value)
        # The C interpreter's own limit is lower and is reported beside it.
        assert "keeps 32" in str(caught.value)

    def test_a_trailing_bit_range_before_a_one_byte_first_name(self):
        """Its length byte 0x01 would read as the bit range's consume marker."""
        doc = _schema({"name": "a", "type": "u8"}, {"name": "bits", "type": "u8[0:3]"})
        with pytest.raises(sb.UnrepresentableSchemaError, match="consume marker"):
            sb.encode_schema(doc)
        sb.encode_schema(doc, names=False)
        doc["fields"][0]["name"] = "ab"
        assert (
            sb.decode_schema(sb.encode_schema(doc))["fields"][1].get("consume") is None
        )


class TestTheReader:
    BLOB = sb.encode_schema(
        _schema({"name": "soil_temperature", "type": "u16", "ipso": {"object": 3303}})
    )

    def test_it_reports_the_carried_name_never_the_ipso_one(self):
        field = sb.decode_schema(self.BLOB)["fields"][0]
        assert field["name"] == "soil_temperature"
        assert field["ipso"] == {"object": 3303}

    def test_an_old_version_is_refused(self):
        v1 = bytes([0x50, 0x53, 0x01, 0x00, 0x01, 0x02, 0x00, 0xE7, 0x0C])
        with pytest.raises(
            sb.BinarySchemaFormatError, match="version 1 is not supported"
        ):
            sb.decode_schema(v1)

    def test_a_truncated_table_is_refused(self):
        with pytest.raises(sb.BinarySchemaFormatError, match="name of field 0"):
            sb.decode_schema(self.BLOB[:-1])
        with pytest.raises(sb.BinarySchemaFormatError, match="name table entry 0"):
            sb.decode_schema(self.BLOB[:9])

    def test_trailing_bytes_are_refused(self):
        with pytest.raises(sb.BinarySchemaFormatError, match="after the last name"):
            sb.decode_schema(self.BLOB + b"\x00")

    def test_an_empty_name_on_a_value_is_refused(self):
        blob = bytes([0x50, 0x53, 3, 0, 0x81, 0x01, 0, 0, 0, 0])
        with pytest.raises(sb.BinarySchemaFormatError, match="only a skip"):
            sb.decode_schema(blob)

    def test_a_scale_reads_back_as_written(self):
        """0xFE is 0.01; every byte with the high bit set was read as 1/2^n."""
        blob = sb.encode_schema(_schema({"name": "t", "type": "s16", "mult": 0.01}))
        assert sb.decode_schema(blob)["fields"][0]["mult"] == pytest.approx(0.01)


class TestTheTools:
    def test_dump_and_info_show_the_names(self, tmp_path):
        src = REPO_ROOT / "examples" / "env_sensor.yaml"
        blob = tmp_path / "env.bin"
        run = [sys.executable, str(TOOLS / "schema_binary.py")]
        subprocess.run(
            run + ["encode", str(src), "-o", str(blob)], check=True, capture_output=True
        )
        dump = subprocess.run(
            run + ["dump", str(blob)], check=True, capture_output=True, text=True
        ).stdout
        assert "Field 2: 'battery_mv'  u16" in dump
        assert "name table: yes" in dump
        info = subprocess.run(
            run + ["info", str(src)], check=True, capture_output=True, text=True
        ).stdout
        assert "'battery_mv'" in info
        assert "60 bytes with names (39 in the name table), 21 without" in info

    def test_encode_without_names(self, tmp_path):
        src = REPO_ROOT / "examples" / "env_sensor.yaml"
        done = subprocess.run(
            [
                sys.executable,
                str(TOOLS / "schema_binary.py"),
                "encode",
                str(src),
                "--no-names",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        blob = bytes.fromhex(done.stdout.strip())
        assert blob[4] == 0x04 and len(blob) == 21

    def test_the_python_loader_reads_names_from_the_table(self):
        doc = yaml.safe_load((REPO_ROOT / "examples" / "env_sensor.yaml").read_text())
        loaded = binary_schema_loader.load_binary_schema(sb.encode_schema(doc))
        assert [f.name for f in loaded.fields] == [
            "temperature",
            "humidity",
            "battery_mv",
            "status",
        ]
        with pytest.raises(ValueError, match="carries no names"):
            binary_schema_loader.load_binary_schema(sb.encode_schema(doc, names=False))
        assert not hasattr(binary_schema_loader, "IPSO_NAMES")


class TestNoNameIsDerived:
    @pytest.mark.parametrize(
        "path",
        [
            "include/schema_interpreter.h",
            "include/schema_interpreter.hpp",
            "tools/schema_binary.py",
            "tools/binary_schema_loader.py",
        ],
    )
    def test_no_ipso_to_name_table_is_left(self, path):
        text = (REPO_ROOT / path).read_text()
        for gone in (
            "ipso_to_name",
            "ipsoToName",
            "IPSO_NAMES",
            'case 3316: return "voltage"',
        ):
            assert gone not in text, (path, gone)


@pytest.mark.xdist_group("c-build")
@pytest.mark.skipif(not shutil.which("cc"), reason="no C compiler")
@pytest.mark.parametrize("form", ["names", "nameless"])
def test_c_decodes_each_blob_under_the_schemas_keys(form):
    """Every corpus schema the form carries, loaded by schema_load_binary and decoded over
    its vectors: the values the corpus expects, under exactly the keys the reference
    reports - or, without names, under each field's index and no name at all."""
    done = subprocess.run(
        [sys.executable, str(HARNESS), "--from-blob", form, "--failures"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert " 0 differ" in done.stdout, done.stdout
    attempted = int(done.stdout.split(" of ")[1].split()[0])
    assert attempted >= 64, done.stdout
