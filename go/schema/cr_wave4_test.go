// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import "testing"

// 0.5.2 wave 4. The fixtures in _language-conformance hold the decode of every new type;
// these hold the rejections and the minifloat encoder.
func TestWave4Rejections(t *testing.T) {
	for label, field := range map[string]string{
		"PS-422 signed": "{name: v, type: s16, encoding: bcd}",
		"PS-422 name":   "{name: v, type: u16, encoding: zigzag}",
		"PS-426":        "{name: v, type: u8, match_value: {a: 1}}",
		"PS-430":        "{name: v, type: bitfield_string, length: 1, parts: [[0, 8, octal]]}",
		"PS-364":        "byte_group: {size: 1, fields: [{name: a, type: 'u8[0:3]', endian: little}]}",
	} {
		if _, err := ParseSchema("name: p\nfields:\n  - " + field + "\n"); err == nil {
			t.Errorf("%s: accepted", label)
		}
	}
}

func TestWave4MinifloatEncoder(t *testing.T) {
	for _, c := range []struct {
		t     FieldType
		value float64
		word  uint64
	}{{TypeUFlt16, 0.5, 0xF800}, {TypeUFlt16, 0.001, 0x6831}, {TypeSFlt16, -0.25, 0xF400}, {TypeSFlt24, 1.0, 0x3F0000}} {
		word, err := encodeMinifloat(c.t, c.value)
		if err != nil || word != c.word {
			t.Errorf("%s %v: %#x %v, want %#x", c.t, c.value, word, err, c.word)
		}
	}
	if _, err := encodeMinifloat(TypeUFlt16, 1.0); err == nil {
		t.Error("uflt16 1.0 is out of range (PS-420)")
	}
}
