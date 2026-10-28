"""generate_firmware_codec.py: pack for every port, and a real tlv pack and unpack.

Three defects, reported from a device build (an STM32L476 driving an xDot):

- A port declared `direction: uplink` got only an unpack, so the device had no
  pack for what it sends. Every port now gets both: a device packs uplinks and
  unpacks downlinks, and a host-side tool does the reverse.
- A tlv pack wrote every case, one after another, so a one-of command came out
  as 9 octets holding all three commands. It now writes the one entry its tag
  members select (a tag no case takes is -1).
- A tlv unpack was a comment and `return 0`: a device ignored every command and
  saw no error. It now reads entries to the end, bounded by each entry's length
  where the tlv has one, and follows `unknown`.

Each test generates a header, compiles a small C program against it with the
host compiler and runs it.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
from generate_firmware_codec import FirmwareGenerator  # noqa: E402

CC = shutil.which("cc") or shutil.which("gcc")
pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler")

DEMO = {
    "name": "demo",
    "version": 1,
    "endian": "big",
    "ports": {
        2: {
            "direction": "uplink",
            "fields": [
                {"name": "seq", "type": "u8"},
                {"name": "light_raw", "type": "u16"},
                {"name": "servo_deg", "type": "u8"},
                {"name": "status", "type": "u8"},
            ],
        },
        3: {
            "direction": "downlink",
            "fields": [
                {
                    "tlv": {
                        "tag_fields": [{"name": "cmd", "type": "u8"}],
                        "tag_key": ["cmd"],
                        "cases": {
                            "[1]": [{"name": "buzz_ms", "type": "u8", "mult": 10}],
                            "[2]": [{"name": "servo_deg", "type": "u8"}],
                            "[3]": [
                                {"name": "red", "type": "u8"},
                                {"name": "green", "type": "u8"},
                                {"name": "blue", "type": "u8"},
                            ],
                        },
                    }
                }
            ],
        },
    },
}

PRELUDE = """
#include <stdio.h>
#include <string.h>
#include "codec.h"
static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\\n", __LINE__, #c); fails++; } } while (0)
static int same(const uint8_t *a, int n, const char *hex) {
    size_t m = strlen(hex) / 2;
    if (n != (int)m) return 0;
    for (size_t i = 0; i < m; i++) {
        unsigned v; sscanf(hex + 2 * i, "%2x", &v);
        if (a[i] != v) return 0;
    }
    return 1;
}
static size_t bytes(const char *hex, uint8_t *out) {
    size_t m = strlen(hex) / 2;
    for (size_t i = 0; i < m; i++) { unsigned v; sscanf(hex + 2 * i, "%2x", &v); out[i] = (uint8_t)v; }
    return m;
}
"""


def run(tmp_path, schema, body):
    header = FirmwareGenerator(schema, "test").generate()
    assert "use reference interpreter" not in header
    (tmp_path / "codec.h").write_text(header)
    src = tmp_path / "main.c"
    src.write_text(PRELUDE + "int main(void) {\n" + body + "\nreturn fails;\n}\n")
    exe = tmp_path / "main"
    build = subprocess.run(
        [CC, "-std=c99", "-Wall", "-Werror", "-Wno-unused-function",
         "-I", str(tmp_path), "-o", str(exe), str(src)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
    return header


def test_an_uplink_port_has_a_pack(tmp_path):
    run(tmp_path, DEMO, r"""
    demo_port2_t s = {7, 658, 90, 1};
    uint8_t buf[DEMO_PORT2_PAYLOAD_MAX];
    int n = pack_demo_port2(&s, buf);
    CHECK(same(buf, n, "0702925A01"));
    demo_port2_t back;
    CHECK(unpack_demo_port2(buf, (size_t)n, &back) == 5);
    CHECK(back.light_raw == 658 && back.servo_deg == 90);
    """)


def test_a_tlv_pack_writes_the_selected_entry_only(tmp_path):
    run(tmp_path, DEMO, r"""
    uint8_t buf[DEMO_PORT3_PAYLOAD_MAX];
    demo_port3_t c;
    memset(&c, 0, sizeof c); c.cmd = 1; c.buzz_ms = 30;
    CHECK(same(buf, pack_demo_port3(&c, buf), "011E"));
    memset(&c, 0, sizeof c); c.cmd = 2; c.servo_deg = 45;
    CHECK(same(buf, pack_demo_port3(&c, buf), "022D"));
    memset(&c, 0, sizeof c); c.cmd = 3; c.green = 160;
    CHECK(same(buf, pack_demo_port3(&c, buf), "0300A000"));
    c.cmd = 9;
    CHECK(pack_demo_port3(&c, buf) == -1);
    CHECK(DEMO_PORT3_PAYLOAD_MAX == 4);
    """)


def test_a_tlv_unpack_reads_the_command(tmp_path):
    run(tmp_path, DEMO, r"""
    uint8_t in[16]; size_t n; demo_port3_t c;
    n = bytes("011E", in);
    CHECK(unpack_demo_port3(in, n, &c) == 2); CHECK(c.cmd == 1 && c.buzz_ms == 30);
    n = bytes("0300A000", in);
    CHECK(unpack_demo_port3(in, n, &c) == 4); CHECK(c.cmd == 3 && c.green == 160);
    CHECK(c.tlv_present == DEMO_PORT3_CASE_3);
    /* two entries in one frame: each lands in its members, and tlv_present
       says both were there, whatever order they came in */
    n = bytes("022D011E", in);
    CHECK(unpack_demo_port3(in, n, &c) == 4); CHECK(c.buzz_ms == 30 && c.servo_deg == 45);
    CHECK(c.tlv_present == (DEMO_PORT3_CASE_1 | DEMO_PORT3_CASE_2));
    /* the same case twice cannot be held (the reference makes an array) */
    n = bytes("011E0114", in);
    CHECK(unpack_demo_port3(in, n, &c) == -1);
    /* a case cut short is an error, not a partial read */
    n = bytes("0300A0", in);
    CHECK(unpack_demo_port3(in, n, &c) == -1);
    /* an unknown tag with no length stops the loop (unknown: skip):
       the return value falls short of len */
    n = bytes("011E09FF", in);
    CHECK(unpack_demo_port3(in, n, &c) == 2);
    """)


def test_unknown_error_fails_the_unpack(tmp_path):
    schema = {"name": "e", "version": 1, "endian": "big", "fields": [{"tlv": {
        "tag_size": 1, "unknown": "error",
        "cases": {1: [{"name": "a", "type": "u8"}]}}}]}
    run(tmp_path, schema, r"""
    uint8_t in[8]; size_t n; e_t d;
    n = bytes("0105", in);
    CHECK(unpack_e(in, n, &d) == 2); CHECK(d.tlv_tag == 1 && d.a == 5);
    n = bytes("0105020A", in);
    CHECK(unpack_e(in, n, &d) == -1);
    """)


def test_a_length_bounds_each_entry(tmp_path):
    # Composite tag (channel, type), a one-byte length, little-endian.
    schema = {"name": "l", "version": 1, "endian": "little", "fields": [{"tlv": {
        "tag_fields": [{"name": "ch", "type": "u8"}, {"name": "ty", "type": "u8"}],
        "tag_key": ["ch", "ty"], "length_size": 1,
        "cases": {
            "[1, 103]": [{"name": "temp", "type": "s16"}],
            "[2, *]": [{"name": "level", "type": "u8"}],
            "default": [],
        }}}]}
    run(tmp_path, schema, r"""
    uint8_t in[32], buf[32]; size_t n; l_t d;
    n = bytes("016702F6FF", in);
    CHECK(unpack_l(in, n, &d) == 5); CHECK(d.temp == -10);
    CHECK(same(buf, pack_l(&d, buf), "016702F6FF"));
    /* an entry no case names steps over its length (default: []) */
    n = bytes("0901030A0B0C02050107", in);
    CHECK(unpack_l(in, n, &d) == 10); CHECK(d.level == 7 && d.ch == 2 && d.ty == 5);
    /* bytes a case leaves are skipped; a case reading past its
       entry is an error */
    n = bytes("0200020700", in);
    CHECK(unpack_l(in, n, &d) == 5); CHECK(d.level == 7);
    n = bytes("016701F6FF", in);
    CHECK(unpack_l(in, n, &d) == -1);
    /* a length running past the payload */
    n = bytes("016709F6FF", in);
    CHECK(unpack_l(in, n, &d) == -1);
    """)


def test_on_repeat_last_keeps_the_last_entry(tmp_path):
    schema = {"name": "r", "version": 1, "endian": "big", "fields": [{"tlv": {
        "tag_size": 1, "on_repeat": "last",
        "cases": {1: [{"name": "a", "type": "u8"}]}}}]}
    run(tmp_path, schema, r"""
    uint8_t in[8]; size_t n; r_t d;
    n = bytes("01050107", in);
    CHECK(unpack_r(in, n, &d) == 4); CHECK(d.a == 7 && d.tlv_present == R_CASE_1);
    """)
