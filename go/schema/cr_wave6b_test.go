// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"bytes"
	"reflect"
	"strings"
	"testing"
)

// 0.5.2 wave 6b: field-level rules. The fixtures in _language-conformance hold the decode
// and round trip of each construct; these hold what a vector cannot express - the
// rejections, the errors, and the encode rules. Each mirrors a case in
// tests/test_wave6b_field_rules.py.

func wave6bSchema(t *testing.T, fields string) *Schema {
	t.Helper()
	s, err := ParseSchema("name: probe\nfields:\n" + fields)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	return s
}

func expectErrorCiting(t *testing.T, label string, err error, tag string) {
	t.Helper()
	if err == nil {
		t.Errorf("%s: no error, want one citing %s", label, tag)
	} else if !strings.Contains(err.Error(), tag) {
		t.Errorf("%s: %v, want one citing %s", label, err, tag)
	}
}

func TestWave6bSchemaRules(t *testing.T) {
	for _, c := range []struct{ tag, fields string }{
		{"PS-404", "  - {name: a, type: u8, optional: true}\n  - {name: b, type: u8}\n"},
		{"PS-404", "  - {name: r, type: repeat, count: 1, fields: [{name: a, type: u8, optional: true}, {name: b, type: u8}]}\n"},
		{"PS-404", "  - {name: k, type: u8}\n  - match: {field: $k, cases: {1: [{name: a, type: u8, optional: true}, {name: b, type: u8}]}}\n"},
		{"PS-433", "  - {name: _meta, type: u8}\n"},
		{"PS-433", "  - {name: _quality, type: number, value: 1}\n"},
		{"PS-433", "  - {name: o, type: object, fields: [{name: _warnings, type: u8}]}\n"},
		{"PS-416", "  - {name: k, type: u8, var: k}\n  - match: {field: $k, remaining: true, cases: {1: [{name: a, type: u8}]}}\n"},
		{"PS-399", "  - match: {cases: {1: [{name: a, type: u8}]}}\n"},
		{"PS-414", "  - match: {remaining: false, cases: {1: [{name: a, type: u8}]}}\n"},
		{"PS-427", "  - {name: t, type: u8, sentinel: 5}\n"},
		{"PS-427", "  - {name: t, type: u8, sentinel: [x]}\n"},
		{"PS-428", "  - {name: t, type: u8, valid_range: [0, 1], out_of_range: drop}\n"},
	} {
		_, err := ParseSchema("name: probe\nfields:\n" + c.fields)
		expectErrorCiting(t, c.fields, err, c.tag)
	}
	// A trailing run of optional fields, and an internal name that is not reserved, load.
	wave6bSchema(t, "  - {name: a, type: u8}\n  - {name: b, type: u8, optional: true}\n  - {name: c, type: u8, optional: true}\n")
	wave6bSchema(t, "  - {name: _metadata, type: u8}\n")
}

const wave6bOptional = "  - {name: a, type: u8}\n" +
	"  - {name: fw, type: u16, optional: true}\n" +
	"  - {name: ble, type: u16, optional: true}\n"

// PS-402, PS-403.
func TestWave6bOptionalDecode(t *testing.T) {
	s := wave6bSchema(t, wave6bOptional)
	for _, c := range []struct {
		payload []byte
		want    map[string]any
	}{
		{[]byte{1}, map[string]any{"a": uint64(1)}},
		{[]byte{1, 0, 2}, map[string]any{"a": uint64(1), "fw": uint64(2)}},
		{[]byte{1, 0, 2, 0, 3}, map[string]any{"a": uint64(1), "fw": uint64(2), "ble": uint64(3)}},
	} {
		out, err := s.Decode(c.payload)
		if err != nil || !reflect.DeepEqual(out, c.want) {
			t.Errorf("% x: %v %v, want %v", c.payload, out, err, c.want)
		}
	}
	_, err := s.Decode([]byte{1, 2})
	expectErrorCiting(t, "one byte of a u16", err, "PS-403")

	object := wave6bSchema(t, "  - {name: a, type: u8}\n"+
		"  - {name: gps, type: object, optional: true, fields: [{name: lat, type: s16}, {name: lon, type: s16}]}\n")
	if out, err := object.Decode([]byte{1}); err != nil || len(out) != 1 {
		t.Errorf("absent object: %v %v", out, err)
	}
	if out, err := object.Decode([]byte{1, 0, 1, 0, 2}); err != nil || out["gps"] == nil {
		t.Errorf("present object: %v %v", out, err)
	}
	_, err = object.Decode([]byte{1, 0, 1})
	expectErrorCiting(t, "part of an object", err, "PS-403")
}

// PS-405.
func TestWave6bOptionalEncode(t *testing.T) {
	s := wave6bSchema(t, wave6bOptional)
	_, err := s.Encode(map[string]any{"a": 1, "ble": 2})
	expectErrorCiting(t, "ble without fw", err, "PS-405")
	if out, err := s.Encode(map[string]any{"a": 1}); err != nil || !bytes.Equal(out, []byte{1}) {
		t.Errorf("omitted optionals: % x %v, want 01", out, err)
	}
	if out, err := s.Encode(map[string]any{"a": 1, "fw": 2}); err != nil || !bytes.Equal(out, []byte{1, 0, 2}) {
		t.Errorf("one optional: % x %v, want 01 0002", out, err)
	}
}

// PS-414: an enclosing repeat's reserve is not part of what remains.
func TestWave6bMatchRemainingExcludesReserve(t *testing.T) {
	s := wave6bSchema(t, "  - name: r\n    type: repeat\n    until: end\n    reserve: 1\n    max: 1\n"+
		"    fields:\n      - match: {remaining: true, cases: {2: [{name: pair, type: u16}]}}\n"+
		"  - {name: tail, type: u8}\n")
	out, err := s.Decode([]byte{1, 2, 0xFF})
	if err != nil {
		t.Fatal(err)
	}
	r, _ := out["r"].([]any)
	if len(r) != 1 || out["tail"] != uint64(255) {
		t.Fatalf("%v", out)
	}
	if pair := r[0].(map[string]any)["pair"]; pair != uint64(258) {
		t.Errorf("pair %v, want 258", pair)
	}
}

func TestWave6bMatchRemainingEncodesNoDiscriminator(t *testing.T) {
	s := wave6bSchema(t, "  - {name: status, type: u8}\n"+
		"  - match: {remaining: true, cases: {2: [{name: short, type: u16}], 4: [{name: long, type: u32}]}}\n")
	out, err := s.Encode(map[string]any{"status": 1, "long": 16909060})
	if err != nil || !bytes.Equal(out, []byte{1, 1, 2, 3, 4}) {
		t.Errorf("% x %v, want 01 01020304", out, err)
	}
}

// PS-427: the sentinel is the bits read, before an encoding is decoded.
func TestWave6bSentinelBeforeEncoding(t *testing.T) {
	s := wave6bSchema(t, "  - {name: v, type: u8, encoding: bcd, sentinel: [255]}\n")
	if out, err := s.Decode([]byte{0xFF}); err != nil || len(out) != 0 {
		t.Errorf("FF: %v %v, want {}", out, err)
	}
	if out, err := s.Decode([]byte{0x42}); err != nil || out["v"] == nil {
		t.Errorf("42: %v %v, want v 42", out, err)
	} else if n, _ := toFloat64(out["v"]); n != 42 {
		t.Errorf("42: %v, want 42", out["v"])
	}
	// Encoding an omitted reading writes the sentinel's raw bits.
	if out, err := s.Encode(map[string]any{}); err != nil || !bytes.Equal(out, []byte{0xFF}) {
		t.Errorf("encode absent: % x %v, want ff", out, err)
	}
}

func TestWave6bSentinelInABitRangeAndFlagged(t *testing.T) {
	s := wave6bSchema(t, "  - {name: lo, type: 'u8[0:3]', sentinel: [15]}\n"+
		"  - {name: hi, type: 'u8[4:7]', consume: 1}\n")
	out, err := s.Decode([]byte{0x2F})
	if err != nil || out["lo"] != nil || out["hi"] == nil {
		t.Errorf("2F: %v %v, want hi only", out, err)
	}
	f := wave6bSchema(t, "  - {name: mask, type: u8}\n"+
		"  - flagged: {field: mask, groups: [{bit: 0, fields: [{name: t, type: s16, sentinel: [-32768]}]}]}\n"+
		"  - {name: x, type: u8, valid_range: [0, 10]}\n")
	out, err = f.Decode([]byte{1, 0x80, 0, 5})
	if err != nil || out["t"] != nil {
		t.Fatalf("%v %v", out, err)
	}
	if q, _ := out["_quality"].(map[string]string); q["t"] != "absent" || q["x"] != "good" {
		t.Errorf("_quality %v", out["_quality"])
	}
}

// PS-427, PS-428: an omitted reading joins `_quality` only where it is produced.
func TestWave6bQualityOnlyWhereProduced(t *testing.T) {
	withRange := wave6bSchema(t, "  - {name: t, type: s16, sentinel: [-32768]}\n"+
		"  - {name: x, type: u8, valid_range: [0, 10]}\n")
	out, err := withRange.Decode([]byte{0x80, 0, 5})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"x": "good", "t": "absent"}
	if !reflect.DeepEqual(out["_quality"], want) {
		t.Errorf("_quality %v, want %v", out["_quality"], want)
	}
	alone := wave6bSchema(t, "  - {name: t, type: s16, sentinel: [-32768]}\n")
	if out, err := alone.Decode([]byte{0x80, 0}); err != nil || out["_quality"] != nil {
		t.Errorf("%v %v: _quality produced by nothing", out, err)
	}
	// A field declaring valid_range produces `_quality` itself.
	omit := wave6bSchema(t, "  - {name: h, type: u8, valid_range: [0, 100], out_of_range: omit}\n")
	out, err = omit.Decode([]byte{200})
	if err != nil || out["h"] != nil {
		t.Fatalf("%v %v", out, err)
	}
	if !reflect.DeepEqual(out["_quality"], map[string]string{"h": "out_of_range"}) {
		t.Errorf("_quality %v", out["_quality"])
	}
	sentinelRange := wave6bSchema(t, "  - {name: h, type: u8, valid_range: [0, 100], sentinel: [255]}\n")
	out, _ = sentinelRange.Decode([]byte{255})
	if !reflect.DeepEqual(out["_quality"], map[string]string{"h": "absent"}) {
		t.Errorf("_quality %v", out["_quality"])
	}
}

// PS-429: an omitted reading is not bound, under its name or its var.
func TestWave6bOmittedReadingIsNotBound(t *testing.T) {
	s := wave6bSchema(t, "  - {name: t, type: u8, sentinel: [255]}\n"+
		"  - {name: u, type: number, ref: $t, mult: 2}\n")
	// A reference to an unbound name is this decoder's existing error; what matters here
	// is that it does not resolve to the sentinel's value.
	if out, err := s.Decode([]byte{0xFF}); err == nil && out["u"] != nil {
		t.Errorf("a reference to a sentinel reading resolved: %v", out)
	}
	if out, err := s.Decode([]byte{5}); err != nil || out["u"] != 10.0 {
		t.Errorf("05: %v %v, want u 10", out, err)
	}
	r := wave6bSchema(t, "  - {name: h, type: u8, var: hv, valid_range: [0, 100], out_of_range: omit}\n"+
		"  - {name: u, type: number, ref: $hv}\n")
	if out, err := r.Decode([]byte{200}); err == nil && out["u"] != nil {
		t.Errorf("the var of an out-of-range reading was bound: %v", out)
	}
}

// PS-434.
func TestWave6bInternalFieldEncode(t *testing.T) {
	s := wave6bSchema(t, "  - {name: _version, type: u8}\n  - {name: a, type: u8}\n")
	_, err := s.Encode(map[string]any{"a": 1})
	expectErrorCiting(t, "no value, no input", err, "PS-434")
	if err != nil && !strings.Contains(err.Error(), "_version") {
		t.Errorf("the error does not name the field: %v", err)
	}
	if out, err := s.Encode(map[string]any{"_version": 3, "a": 1}); err != nil || !bytes.Equal(out, []byte{3, 1}) {
		t.Errorf("value from the input: % x %v, want 03 01", out, err)
	}

	declared := wave6bSchema(t, "  - {name: _version, type: u8, value: 2}\n  - {name: a, type: u8}\n")
	if out, err := declared.Encode(map[string]any{"_version": 9, "a": 1}); err != nil || !bytes.Equal(out, []byte{2, 1}) {
		t.Errorf("declared value: % x %v, want 02 01", out, err)
	}

	run := wave6bSchema(t, "  - {name: _r, type: 'u8[0:3]'}\n  - {name: v, type: 'u8[4:7]', consume: 1}\n")
	_, err = run.Encode(map[string]any{"v": 1})
	expectErrorCiting(t, "bit range in a run", err, "PS-434")
	if out, err := run.Encode(map[string]any{"v": 1, "_r": 5}); err != nil || !bytes.Equal(out, []byte{0x15}) {
		t.Errorf("bit range from the input: % x %v, want 15", out, err)
	}

	group := wave6bSchema(t, "  - byte_group: {size: 1, fields: [{name: _r, type: 'u8[0:3]'}, {name: v, type: 'u8[4:7]'}]}\n")
	_, err = group.Encode(map[string]any{"v": 1})
	expectErrorCiting(t, "byte_group member", err, "PS-434")

	computed := wave6bSchema(t, "  - {name: a, type: u8}\n  - {name: _double, type: number, ref: $a, mult: 2}\n")
	if out, err := computed.Encode(map[string]any{"a": 4}); err != nil || !bytes.Equal(out, []byte{4}) {
		t.Errorf("internal computed field: % x %v, want 04", out, err)
	}

	padding := wave6bSchema(t, "  - {name: a, type: u8}\n  - {type: skip, length: 2}\n")
	if out, err := padding.Encode(map[string]any{"a": 1}); err != nil || !bytes.Equal(out, []byte{1, 0, 0}) {
		t.Errorf("unnamed padding: % x %v, want 01 0000", out, err)
	}
}

// PS-434 in a flagged group: an internal member that reads bytes writes its value, else
// the input's, else it is an error naming it; an omitted sentinel member writes its
// sentinel's raw bits.
func TestWave6bFlaggedInternalMember(t *testing.T) {
	s := wave6bSchema(t, "  - {name: mask, type: u8}\n"+
		"  - flagged: {field: mask, groups: [{bit: 0, fields: [{name: _r, type: u8}, {name: v, type: u8}]}]}\n")
	_, err := s.Encode(map[string]any{"mask": 1, "v": 2})
	expectErrorCiting(t, "flagged internal member", err, "PS-434")
	if err != nil && !strings.Contains(err.Error(), "_r") {
		t.Errorf("the error does not name the field: %v", err)
	}
	if out, err := s.Encode(map[string]any{"mask": 1, "_r": 9, "v": 2}); err != nil || !bytes.Equal(out, []byte{1, 9, 2}) {
		t.Errorf("% x %v, want 01 09 02", out, err)
	}
	sentinel := wave6bSchema(t, "  - {name: mask, type: u8}\n"+
		"  - flagged: {field: mask, groups: [{bit: 0, fields: [{name: t, type: s16, div: 10, sentinel: [-32768]}, {name: v, type: u8}]}]}\n")
	if out, err := sentinel.Encode(map[string]any{"mask": 1, "v": 2}); err != nil || !bytes.Equal(out, []byte{1, 0x80, 0, 2}) {
		t.Errorf("% x %v, want 01 8000 02", out, err)
	}
}

// PS-434: the discriminator of a match is not inferred from the case the data fits.
func TestWave6bInternalDiscriminatorIsNotInferred(t *testing.T) {
	s := wave6bSchema(t, "  - {name: _kind, type: u8}\n"+
		"  - match: {field: $_kind, cases: {1: [{name: temperature, type: s16}]}}\n")
	_, err := s.Encode(map[string]any{"temperature": 231})
	expectErrorCiting(t, "internal discriminator", err, "PS-434")
}

// PS-464, PS-465.
func TestWave6bLengthNamesAField(t *testing.T) {
	s := wave6bSchema(t, "  - {name: n, type: u8}\n  - {name: b, type: bytes, length: n}\n"+
		"  - {name: s, type: ascii, length: $n}\n  - {name: z, type: skip, length: n}\n  - {name: after, type: u8}\n")
	out, err := s.Decode([]byte{2, 0xAA, 0xBB, 'h', 'i', 0, 0, 7})
	if err != nil || out["b"] != "aabb" || out["s"] != "hi" || out["after"] != uint64(7) {
		t.Errorf("%v %v", out, err)
	}
	unresolved := wave6bSchema(t, "  - {name: b, type: bytes, length: $n}\n")
	_, err = unresolved.Decode([]byte{1})
	expectErrorCiting(t, "unresolved length", err, "PS-465")
	if err != nil && !strings.Contains(err.Error(), "'n'") {
		t.Errorf("the error does not name the field: %v", err)
	}
	later := wave6bSchema(t, "  - {name: b, type: bytes, length: later}\n  - {name: later, type: u8}\n")
	_, err = later.Decode([]byte{1, 2})
	expectErrorCiting(t, "a later field", err, "PS-465")

	// Encoding writes the count from its own field and the bytes from the value.
	plain := wave6bSchema(t, "  - {name: n, type: u8}\n  - {name: b, type: bytes, length: $n}\n")
	if out, err := plain.Encode(map[string]any{"n": 2, "b": "aabb"}); err != nil || !bytes.Equal(out, []byte{2, 0xAA, 0xBB}) {
		t.Errorf("encode: % x %v, want 02 aabb", out, err)
	}
}
