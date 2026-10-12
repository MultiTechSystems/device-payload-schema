"""Smoke test: bindings/python/schema_native.py against a freshly built libschema.so.

Builds bindings/c/schema_ffi.c as a shared library in a temp directory, points the
binding at it, and decodes one payload through ctypes. Skipped where there is no C
compiler. The Go and Node bindings are not exercised here (they need cgo / node-gyp).
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# temperature s16 mult 0.01, humidity u8 mult 0.5, battery u16, with a name table.
BLOB = bytes(
    [0x50, 0x53, 0x03, 0x00, 0x83]
    + [0x12, 0xFE, 0xE7, 0x0C]
    + [0x01, 0x81, 0xE8, 0x0C]
    + [0x02, 0x00, 0xF4, 0x0C]
    + [11] + list(b"temperature")
    + [8] + list(b"humidity")
    + [7] + list(b"battery")
)
PAYLOAD = bytes([0x09, 0x29, 0x82, 0x0C, 0xE4])


@pytest.fixture()
def native(tmp_path, monkeypatch):
    cc = shutil.which("gcc") or shutil.which("cc")
    if not cc:
        pytest.skip("no C compiler")
    lib = tmp_path / "libschema.so"
    subprocess.run(
        [cc, "-O2", "-fPIC", "-shared", "-I", str(ROOT / "include"),
         "-o", str(lib), str(ROOT / "bindings/c/schema_ffi.c")],
        check=True,
    )
    monkeypatch.syspath_prepend(str(ROOT / "bindings/python"))
    monkeypatch.chdir(tmp_path)
    import schema_native

    monkeypatch.setattr(schema_native.NativeSchema, "_lib", None)
    if not schema_native.NativeSchema.is_available():
        pytest.skip("libschema.so not loadable on this platform")
    return schema_native


def test_decode_through_ctypes(native):
    assert native.NativeSchema.get_version() == "1.0.0"
    s = native.NativeSchema.from_binary(BLOB)
    assert s.field_count == 3
    out = s.decode(PAYLOAD)
    assert out["battery"] == 3300
    assert out["temperature"] == pytest.approx(23.45)
    assert out["humidity"] == pytest.approx(65.0)


def test_short_payload_raises_decode_error(native):
    s = native.NativeSchema.from_binary(BLOB)
    with pytest.raises(native.SchemaError, match="Decode error -3"):
        s.decode(PAYLOAD[:1])
