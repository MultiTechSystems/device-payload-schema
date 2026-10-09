// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"encoding/hex"
	"os"
	"strings"
	"testing"
)

// CR-2026-085 (bytes after the last field, a cut-off tlv tag) and CR-2026-086 (the
// order of a field's arithmetic, amended: valid_range before the lookup).
//
// The fixtures _language-conformance/leftover-bytes.yaml, range-before-lookup.yaml and
// match-default-skip.yaml hold every implementation to the decodes. What a fixture
// cannot state - an error, a warning not reported twice, an encoder's output - is here,
// mirroring tests/test_cr_2026_085_086.py.

const cr085One = "name: probe\nfields:\n  - {name: a, type: u8}\n"

func cr085Decode(t *testing.T, yaml, payload string) (map[string]any, error) {
	t.Helper()
	s, err := ParseSchema(yaml)
	if err != nil {
		t.Fatalf("ParseSchema: %v", err)
	}
	data, _ := hex.DecodeString(payload)
	return s.Decode(data)
}

func cr085Warnings(out map[string]any) []string {
	w, _ := out["_warnings"].([]string)
	return w
}

func cr085Leftover(warnings []string) []string {
	var found []string
	for _, w := range warnings {
		if strings.Contains(w, "PS-472") {
			found = append(found, w)
		}
	}
	return found
}

// PS-472: the decode is reported as it would be otherwise, with a warning stating the
// offset of the first byte not decoded and the count to the end.
func TestCR085BytesAfterTheLastFieldAreReported(t *testing.T) {
	for payload, warned := range map[string]string{"2a": "", "2a0102": "offset 1"} {
		out, err := cr085Decode(t, cr085One, payload)
		if err != nil {
			t.Fatalf("%s: %v", payload, err)
		}
		if v, _ := toInt(out["a"]); v != 42 {
			t.Errorf("%s: a = %v", payload, out["a"])
		}
		found := cr085Leftover(cr085Warnings(out))
		if warned == "" {
			if len(found) != 0 {
				t.Errorf("%s: unexpected %v", payload, found)
			}
			continue
		}
		want := "2 byte(s) after the last field left undecoded, from offset 1 (PS-472)"
		if len(found) != 1 || found[0] != want {
			t.Errorf("%s: warnings %v, want [%q]", payload, found, want)
		}
	}
}

// A PS-302 warning already says what was left: the unknown tag is consumed but not
// decoded, and no second warning is reported.
func TestCR085APS302WarningIsTheLeftoverWarning(t *testing.T) {
	schema := "name: probe\nfields:\n  - tlv:\n      tag_size: 1\n      cases:\n        1:\n          - {name: a, type: u8}\n"
	out, err := cr085Decode(t, schema, "012a09ff")
	if err != nil {
		t.Fatal(err)
	}
	w := cr085Warnings(out)
	if len(w) != 1 || !strings.Contains(w[0], "left undecoded") || len(cr085Leftover(w)) != 0 {
		t.Errorf("warnings %v", w)
	}
}

// PS-477: fewer bytes than a tag at the start of a tlv entry is an error. Go stopped the
// loop and reported a complete decode.
func TestCR085ATagCutOffAtTheEndIsAnError(t *testing.T) {
	cases := map[string][2]string{
		"tag_size": {"name: probe\nfields:\n  - tlv:\n      tag_size: 2\n      cases:\n        1:\n          - {name: a, type: u8}\n",
			"00012a00"},
		"tag_fields": {"name: probe\nfields:\n  - tlv:\n      tag_fields:\n        - {name: ch, type: u8}\n        - {name: ty, type: u8}\n      tag_key: [ch, ty]\n      cases:\n        \"[1, 2]\":\n          - {name: a, type: u8}\n",
			"01022a01"},
	}
	for label, c := range cases {
		_, err := cr085Decode(t, c[0], c[1])
		if err == nil || !strings.Contains(err.Error(), "PS-477") ||
			!strings.Contains(err.Error(), "offset 3") {
			t.Errorf("%s: error %v", label, err)
		}
	}
}

// Measured on a real schema: ws50x's port 85 frame with one byte after a complete entry
// decoded successfully in Go.
func TestCR085CutOffTagOnACorpusSchema(t *testing.T) {
	raw, err := os.ReadFile("../../schemas/devices/milesight/ws50x.yaml")
	if err != nil {
		t.Skip(err)
	}
	s, err := ParseSchema(string(raw))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.DecodeWithPort([]byte{0xff, 0x29, 0x11, 0x11}, 85); err == nil ||
		!strings.Contains(err.Error(), "PS-477") {
		t.Errorf("error %v", err)
	}
}

// Where the tag fits and the length does not, no entry is decoded: the leftover is
// counted from the tag.
func TestCR085ALengthCutOffLeavesTheEntryOverFromItsTag(t *testing.T) {
	schema := "name: probe\nfields:\n  - tlv:\n      tag_size: 1\n      length_size: 2\n      cases:\n        1:\n          - {name: a, type: u8}\n"
	out, err := cr085Decode(t, schema, "0100012a0100")
	if err != nil {
		t.Fatal(err)
	}
	found := cr085Leftover(cr085Warnings(out))
	if len(found) != 1 || !strings.Contains(found[0], "offset 4") || !strings.HasPrefix(found[0], "2 byte") {
		t.Errorf("warnings %v", cr085Warnings(out))
	}
}

// PS-472 is about the selected field list: a port schema is reported after selection.
func TestCR085LeftoverOnAPortSchema(t *testing.T) {
	schema := "name: probe\nports:\n  3:\n    fields:\n      - {name: a, type: u8}\n"
	s, err := ParseSchema(schema)
	if err != nil {
		t.Fatal(err)
	}
	out, err := s.DecodeWithPort([]byte{0x2a, 0x00}, 3)
	if err != nil {
		t.Fatal(err)
	}
	if found := cr085Leftover(cr085Warnings(out)); len(found) != 1 || !strings.Contains(found[0], "1 byte") {
		t.Errorf("warnings %v", cr085Warnings(out))
	}
}

// PS-474: the leftover bytes were never data, so they are not re-encoded.
func TestCR085AnEncoderWritesNothingAfterTheLastField(t *testing.T) {
	s, _ := ParseSchema(cr085One)
	out, err := s.Decode([]byte{0x2a, 0x01, 0x02})
	if err != nil {
		t.Fatal(err)
	}
	delete(out, "_warnings")
	enc, err := s.Encode(out)
	if err != nil || hex.EncodeToString(enc) != "2a" {
		t.Errorf("encode = %x, %v", enc, err)
	}
}

const cr086Guarded = "name: probe\nfields:\n  - {name: x, type: u8}\n" +
	"  - {name: v, type: number, ref: $x, guard: {when: [{field: $x, gt: 0}], else: 99}, " +
	"valid_range: [0, 10], out_of_range: omit}\n"

// PS-443 as amended: a guard that reports its `else` ends the sequence, so 99 is
// reported though the range stops at 10.
func TestCR086AGuardElseIsNotComparedWithTheRange(t *testing.T) {
	out, err := cr085Decode(t, cr086Guarded, "00")
	if err != nil {
		t.Fatal(err)
	}
	if v, _ := toFloat64(out["v"]); v != 99 {
		t.Errorf("v = %v", out["v"])
	}
	if q, _ := out["_quality"].(map[string]string); q["v"] != "good" {
		t.Errorf("_quality %v", out["_quality"])
	}
}

func TestCR086AValueTheGuardLetsThroughIsCompared(t *testing.T) {
	out, err := cr085Decode(t, cr086Guarded, "20")
	if err != nil {
		t.Fatal(err)
	}
	if _, present := out["v"]; present {
		t.Errorf("v reported: %v", out["v"])
	}
	if q, _ := out["_quality"].(map[string]string); q["v"] != "out_of_range" {
		t.Errorf("_quality %v", out["_quality"])
	}
}

// PS-475: a looked-up field is compared on its number, and an omitted one is not looked
// up - a mapping lookup with no entry for it would otherwise omit it as unmapped, and a
// sequence lacking the index would fail the decode.
func TestCR086RangeBeforeLookup(t *testing.T) {
	schema := "name: probe\nfields:\n" +
		"  - {name: m, type: u8, valid_range: [0, 1], out_of_range: omit, lookup: {0: off, 1: on, 5: odd}}\n" +
		"  - {name: s, type: u8, valid_range: [0, 1], lookup: [off, on, mid, high, x, y]}\n"
	out, err := cr085Decode(t, schema, "0505")
	if err != nil {
		t.Fatal(err)
	}
	q, _ := out["_quality"].(map[string]string)
	if _, present := out["m"]; present || q["m"] != "out_of_range" {
		t.Errorf("m: %v, quality %v", out["m"], q)
	}
	if out["s"] != "y" || q["s"] != "out_of_range" {
		t.Errorf("s: %v, quality %v", out["s"], q)
	}
}
