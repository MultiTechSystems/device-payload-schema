// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"bytes"
	"encoding/hex"
	"testing"
)

// CR-2026-115 (PS-535): a read or skip past the end is an error identifying the field.
// The texts are the spec prototype's (la-payload-schema main c932961) and the Python
// reference's, compared exactly so the five implementations stay equal strings. Go gave
// the offset alone: "buffer underflow: need 4 bytes at offset 1, but only 2 remaining".
func TestCR2026115AShortReadNamesItsField(t *testing.T) {
	cases := []struct{ label, fields, payload, want string }{
		{"u16", "- {name: n, type: u16}", "01",
			"Field 'n': Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"unnamed skip", "- {type: skip, length: 3}", "",
			"Field an unnamed skip: Buffer underflow: need 3 bytes at offset 0, but only 0 remaining (PS-535)"},
		{"named skip", "- {name: pad, type: skip, length: 3}", "00",
			"Field 'pad': Buffer underflow: need 3 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"ascii", "- {name: s, type: ascii, length: 4}", "4142",
			"Field 's': Buffer underflow: need 4 bytes at offset 0, but only 2 remaining (PS-535)"},
		{"hex", "- {name: h, type: hex, length: 4}", "41",
			"Field 'h': Buffer underflow: need 4 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"bytes", "- {name: b, type: bytes, length: 4}", "41",
			"Field 'b': Buffer underflow: need 4 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"f32", "- {name: f, type: f32}", "0102",
			"Field 'f': Buffer underflow: need 4 bytes at offset 0, but only 2 remaining (PS-535)"},
		{"udec", "- {name: ud, type: udec}", "",
			"Field 'ud': Buffer underflow: need 1 bytes at offset 0, but only 0 remaining (PS-535)"},
		{"word-ordered", "- {name: w, type: u32le16}", "0102",
			"Field 'w': Buffer underflow: need 4 bytes at offset 0, but only 2 remaining (PS-535)"},
		{"internal", "- {name: _k, type: u16}", "01",
			"Field '_k': Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"enum", "- {name: e, type: enum, base: u16, values: {1: one}}", "00",
			"Field 'e': Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"variable length", "- {name: n, type: u8}\n- {name: s, type: ascii, length: $n}", "0541",
			"Field 's': Buffer underflow: need 5 bytes at offset 1, but only 1 remaining (PS-535)"},
		// The innermost field is named, and nothing enclosing it prefixes the text.
		{"object member", "- {name: o, type: object, fields: [{name: x, type: u16}]}", "01",
			"Field 'x': Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"counted element member", "- {name: r, type: repeat, count: 2, fields: [{name: v, type: u16}]}", "000102",
			"Field 'v': Buffer underflow: need 2 bytes at offset 2, but only 1 remaining (PS-535)"},
		{"named element", "- {name: r, type: repeat, count: 2, element: {name: v, type: u16}}", "000102",
			"Field 'v': Buffer underflow: need 2 bytes at offset 2, but only 1 remaining (PS-535)"},
		{"unnamed element", "- {name: r, type: repeat, count: 2, element: {type: u16}}", "000102",
			"Field 'element': Buffer underflow: need 2 bytes at offset 2, but only 1 remaining (PS-535)"},
		{"tag case member", "- tlv: {tag_size: 1, cases: {1: [{name: t, type: u16}]}}", "0101",
			"Field 't': Buffer underflow: need 2 bytes at offset 1, but only 1 remaining (PS-535)"},
		{"tag case member under reserve", "- tlv: {tag_size: 1, reserve: 1, cases: {1: [{name: t, type: u16}]}}", "010102",
			"Field 't': Buffer underflow: need 2 bytes at offset 1, but only 1 remaining (PS-535)"},
		{"selected case member", "- {name: k, type: u8, var: k}\n- match: {field: $k, cases: {1: [{name: m, type: u16}]}}", "0101",
			"Field 'm': Buffer underflow: need 2 bytes at offset 1, but only 1 remaining (PS-535)"},
		{"present group member", "- {name: fl, type: u8}\n- flagged: {field: fl, groups: [{bit: 0, fields: [{name: g, type: u16}]}]}", "0101",
			"Field 'g': Buffer underflow: need 2 bytes at offset 1, but only 1 remaining (PS-535)"},
		{"definition member", "- $ref: '#/definitions/d'", "01",
			"Field 'rr': Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
		// A bit range and a bool read their byte(s) without consuming them.
		{"bit range", "- {name: b, type: 'u16[0:3]', consume: 2}", "01",
			"Field 'b': Buffer underflow at peek offset 0 (PS-535)"},
		{"bool", "- {name: bo, type: bool, bit: 1}", "",
			"Field 'bo': Buffer underflow at peek offset 0 (PS-535)"},
		// `consume` past the end is a read past the end too.
		{"bit range consume", "- {name: b, type: 'u8[0:3]', consume: 2}", "01",
			"Field 'b': Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"bool consume", "- {name: bo, type: bool, consume: 2}", "01",
			"Field 'bo': Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"shared byte member", "- byte_group: {size: 2, fields: [{name: a, type: 'u16[0:3]'}, {name: c, type: 'u16[4:7]'}]}", "01",
			"Field 'a': Buffer underflow at peek offset 0 (PS-535)"},
		{"shared byte size", "- byte_group: {size: 2, fields: [{name: a, type: 'u8[0:3]'}, {name: c, type: 'u8[4:7]'}]}", "01",
			"Field an unnamed byte_group: Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
		// An inline discriminator is named by the construct's `name`, where it has one.
		{"named discriminator", "- match: {length: 2, name: kind, cases: {1: [{name: m, type: u8}]}}", "00",
			"Field 'kind': Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
		{"unnamed discriminator", "- match: {length: 2, cases: {1: [{name: m, type: u8}]}}", "00",
			"Field an unnamed match: Buffer underflow: need 2 bytes at offset 0, but only 1 remaining (PS-535)"},
	}
	for _, c := range cases {
		t.Run(c.label, func(t *testing.T) {
			src := "name: p\nendian: big\ndefinitions:\n  d:\n    fields:\n    - {name: rr, type: u16}\nfields:\n" + c.fields + "\n"
			s, err := ParseSchema(src)
			if err != nil {
				t.Fatal(err)
			}
			payload, _ := hex.DecodeString(c.payload)
			out, err := s.Decode(payload)
			if err == nil {
				t.Fatalf("want %q, decoded %v", c.want, out)
			}
			if err.Error() != c.want {
				t.Errorf("got  %q\nwant %q", err.Error(), c.want)
			}
		})
	}
}

// The exceptions PS-535 names keep their own meaning: `remaining` at the end is empty,
// and an element too short at the end of an until-end array is a ragged tail (PS-343).
func TestCR2026115TheExceptionsAreUnchanged(t *testing.T) {
	s, err := ParseSchema("name: p\nfields:\n- {name: a, type: u8}\n- {name: rest, type: hex, length: remaining}\n")
	if err != nil {
		t.Fatal(err)
	}
	out, err := s.Decode([]byte{1})
	if err != nil || out["rest"] != "" {
		t.Errorf("remaining at the end: got %v, %v", out, err)
	}
	s, err = ParseSchema("name: p\nfields:\n- {name: r, type: repeat, until: end, fields: [{name: v, type: u16}]}\n")
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Decode([]byte{0, 1, 2})
	want := "repeat 'r' ends in a ragged tail: 1 byte(s) at offset 2, fewer than the 2 an element takes (PS-343)"
	if err == nil || err.Error() != want {
		t.Errorf("got %v, want %q", err, want)
	}
}

func asciiSchema(t *testing.T) *Schema {
	t.Helper()
	s, err := ParseSchema("name: p\nendian: big\nfields:\n- {name: label, type: ascii, length: 6}\n")
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// CR-2026-114 (PS-534, PS-538): a byte above 0x7F is the ISO 8859-1 character of the
// same number, with PS-073's warning; only trailing 0x00 is padding. Go returned the
// bytes themselves, which are not valid UTF-8 and so not a valid JSON string.
func TestCR2026114HighBytesDecodeAsLatin1(t *testing.T) {
	s := asciiSchema(t)
	cases := []struct {
		payload, want string
		warned        bool
	}{
		{"41E900000000", "A\u00e9", true},
		{"80A0FF410000", "\u0080\u00a0\u00ffA", true},
		{"414200430000", "AB\x00C", false},
		{"204120000000", " A ", false},
		{"000000000000", "", false},
	}
	for _, c := range cases {
		payload, _ := hex.DecodeString(c.payload)
		out, err := s.Decode(payload)
		if err != nil {
			t.Fatalf("%s: %v", c.payload, err)
		}
		if out["label"] != c.want {
			t.Errorf("%s: got %q, want %q", c.payload, out["label"], c.want)
		}
		warnings, _ := out["_warnings"].([]string)
		want := "Field 'label': a byte above 0x7F in an ascii value (PS-073, PS-538)"
		if c.warned && (len(warnings) != 1 || warnings[0] != want) {
			t.Errorf("%s: warnings %q, want [%q]", c.payload, warnings, want)
		}
		if !c.warned && len(warnings) != 0 {
			t.Errorf("%s: unexpected warnings %q", c.payload, warnings)
		}
	}
}

// CR-2026-114 (PS-537): a short value is padded; a long one, or one holding a character
// above U+00FF, is rejected naming the field; U+0080..U+00FF is written as its byte with
// a warning. Go truncated ("ABCDEF" to 41424344) and wrote UTF-8, splitting a character
// ("ABCé" to 414243c3 under length 4).
func TestCR2026114AsciiEncoding(t *testing.T) {
	s := asciiSchema(t)
	r, err := s.EncodeToResult(map[string]any{"label": "AB"}, 0)
	if err != nil || !bytes.Equal(r.Payload, []byte{0x41, 0x42, 0, 0, 0, 0}) || len(r.Warnings) != 0 {
		t.Errorf("padding: got %v, %v", r, err)
	}

	r, err = s.EncodeToResult(map[string]any{"label": "ABC\u00e9"}, 0)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(r.Payload, []byte{0x41, 0x42, 0x43, 0xE9, 0, 0}) {
		t.Errorf("latin-1: got %x", r.Payload)
	}
	want := "Field 'label': a character above U+007F in an ascii value (PS-537)"
	if len(r.Warnings) != 1 || r.Warnings[0] != want {
		t.Errorf("latin-1 warnings %q, want [%q]", r.Warnings, want)
	}

	rejected := map[string]string{
		"ABCDEFG":     "Field 'label': an ascii value of 7 bytes is longer than its length of 6 (PS-537)",
		"\u20ac":      "Field 'label': an ascii value holds a character above U+00FF, which has no byte (PS-537)",
		"A\U0001F600": "Field 'label': an ascii value holds a character above U+00FF, which has no byte (PS-537)",
	}
	for value, want := range rejected {
		if out, err := s.Encode(map[string]any{"label": value}); err == nil || err.Error() != want {
			t.Errorf("%q: got %x, %v; want %q", value, out, err, want)
		}
	}
}

// PS-537 reverses PS-538: every decoded value re-encodes to its own bytes.
func TestCR2026114Latin1RoundTrips(t *testing.T) {
	s := asciiSchema(t)
	for b := 0; b < 256; b++ {
		payload := []byte{0x41, byte(b), 0x42, 0x43, 0x44, 0x45}
		out, err := s.Decode(payload)
		if err != nil {
			t.Fatal(err)
		}
		delete(out, "_warnings")
		back, err := s.Encode(out)
		if err != nil || !bytes.Equal(back, payload) {
			t.Errorf("byte %02x: %x -> %v -> %x, %v", b, payload, out, back, err)
		}
	}
}

// A rejection inside a tag case reaches the caller verbatim, as at the top level.
func TestCR2026114RejectionInsideACaseIsVerbatim(t *testing.T) {
	s, err := ParseSchema("name: p\nfields:\n- tlv: {tag_size: 1, cases: {1: [{name: s, type: ascii, length: 2}]}}\n")
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Encode(map[string]any{"s": "ABC"})
	want := "Field 's': an ascii value of 3 bytes is longer than its length of 2 (PS-537)"
	if err == nil || err.Error() != want {
		t.Errorf("got %v, want %q", err, want)
	}
}
