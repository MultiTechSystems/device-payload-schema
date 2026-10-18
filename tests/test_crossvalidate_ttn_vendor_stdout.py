"""crossvalidate_ttn.py must read the vendor decoder's result even when the codec prints.

elvaco cmi4111's decoder logs its hex array with console.log. The driver's JSON was read
as the whole of stdout, so the parse failed and xval reported that the vendor decoder did
not run. The result now goes on a marked line, and anything else on stdout is ignored.
"""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import crossvalidate_ttn as cv  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

DECODER = """
function decodeUplink(input) {
  console.log('hex_array', input.bytes);
  console.log('{"not": "the result"}');
  return { data: { first: input.bytes[0], port: input.fPort } };
}
"""


def test_vendor_output_on_stdout_is_ignored(tmp_path):
    js = tmp_path / "chatty.js"
    js.write_text(DECODER)
    out = cv.run_vendor_decoder(js, [{"hex": "2a01", "fPort": 3}])
    assert out == [{"data": {"first": 42, "port": 3}}]


def test_quiet_decoder_still_reads(tmp_path):
    js = tmp_path / "quiet.js"
    js.write_text(DECODER.replace("console.log", "void"))
    assert cv.run_vendor_decoder(js, [{"hex": "07", "fPort": 1}]) == [
        {"data": {"first": 7, "port": 1}}
    ]


def test_no_marked_line_is_no_result():
    assert cv._vendor_result("hex_array [1, 2]\n") is None
