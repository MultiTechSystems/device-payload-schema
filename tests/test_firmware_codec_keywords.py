"""A field (or schema) named by a C or C++ keyword gets a trailing underscore in the generated header, so the header
compiles as C and from C++ (a field `switch` used to generate `uint16_t switch;`, which no compiler takes)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import hashlib  # noqa: E402

from generate_firmware_codec import C_KEYWORDS, FirmwareGenerator, to_c, to_upper  # noqa: E402

SCHEMA = {
    "name": "probe_keywords",
    "endian": "big",
    "ports": {
        10: {"direction": "uplink", "fields": [
            {"name": "switch", "type": "u16"}, {"name": "class", "type": "u8"}, {"name": "on", "type": "u8"}]},
        20: {"direction": "downlink", "fields": [{"name": "default", "type": "u8"}, {"name": "new", "type": "u8"}]},
    },
}


# Other generators that name this header's identifiers keep the same keyword list; equal digests keep them equal.
KEYWORDS_SHA256 = "00627d1daf50c9cc1363ed957d058b15cbf5c511d92936f45dc02e0d25c50222"


def test_the_keyword_list_is_the_agreed_one():
    assert hashlib.sha256(" ".join(sorted(C_KEYWORDS)).encode()).hexdigest() == KEYWORDS_SHA256


def test_keywords_are_escaped_and_other_names_are_not():
    assert [to_c(n) for n in ("switch", "class", "bool", "Switch", "on", "switch_addr", "my-field")] == \
        ["switch_", "class_", "bool_", "switch_", "on", "switch_addr", "my_field"]
    assert to_upper("switch") == "SWITCH_"


def test_a_header_with_keyword_fields_compiles_as_c_and_cpp(tmp_path):
    code = FirmwareGenerator(SCHEMA, "inline").generate()
    assert "uint16_t switch_;" in code and "uint8_t class_;" in code and "uint8_t default_;" in code
    assert "uint8_t new_;" in code
    assert "uint8_t on;" in code
    h = tmp_path / "probe_keywords_codec.h"
    h.write_text(code)
    src = tmp_path / "use.c"
    src.write_text('#include <string.h>\n#include "probe_keywords_codec.h"\n'
                   "int main(void) {\n"
                   "    uint8_t buf[PROBE_KEYWORDS_PORT10_PAYLOAD_MAX + PROBE_KEYWORDS_PORT20_PAYLOAD_MAX];\n"
                   "    probe_keywords_port10_t a, b; memset(&a, 0, sizeof a);\n"
                   "    a.switch_ = 0x1234; a.class_ = 7; a.on = 1;\n"
                   "    int n = pack_probe_keywords_port10(&a, buf);\n"
                   "    if (n != 4 || unpack_probe_keywords_port10(buf, (size_t)n, &b) != 4) return 1;\n"
                   "    if (!(b.switch_ == 0x1234 && b.class_ == 7 && b.on == 1)) return 2;\n"
                   "    probe_keywords_port20_t c, e; memset(&c, 0, sizeof c);\n"
                   "    c.default_ = 3; c.new_ = 4;\n"
                   "    n = pack_probe_keywords_port20(&c, buf);\n"
                   "    if (n != 2 || unpack_probe_keywords_port20(buf, (size_t)n, &e) != 2) return 3;\n"
                   "    return !(e.default_ == 3 && e.new_ == 4);\n}\n")
    for cc, std in (("cc", "-std=c11"), ("c++", "-std=c++17")):
        if shutil.which(cc) is None:
            pytest.skip(f"no {cc}")
        exe = tmp_path / f"use_{cc.replace('+', 'p')}"
        args = [cc, std, "-Wall", "-Wextra", "-Werror", str(src), "-o", str(exe)]
        if cc == "c++":
            args[1:1] = ["-x", "c++"]
        r = subprocess.run(args, capture_output=True, text=True, cwd=tmp_path)
        assert r.returncode == 0, f"{cc}: {r.stderr}"
        assert subprocess.run([str(exe)]).returncode == 0, f"{cc}: the keyword fields did not round-trip"
