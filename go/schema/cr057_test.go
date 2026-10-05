// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"encoding/hex"
	"testing"
)

// CR-2026-057: a zero divisor (PS-100) and the log of a non-positive number (PS-117)
// leave the field absent, never NaN (PS-282), and the later fields still decode.
func TestCR057ArithmeticWithNoValueOmitsTheField(t *testing.T) {
	cases := []struct{ label, field, raw string }{
		{"bare div 0", "type: u8\n    div: 0", "07"},
		{"stage div 0", "type: u8\n    transform: [{div: 0}]", "07"},
		{"log10 of 0", "type: u8\n    transform: [{log10: true}]", "00"},
		{"log of -1", "type: s8\n    transform: [{log: true}]", "ff"},
		{"log then lookup", "type: u8\n    transform: [{log: true}]\n    lookup: {0: zero}", "00"},
	}
	for _, c := range cases {
		s, err := ParseSchema("name: p\nfields:\n  - name: v\n    " + c.field + "\n  - name: w\n    type: u8\n")
		if err != nil {
			t.Fatalf("%s: %v", c.label, err)
		}
		payload, _ := hex.DecodeString(c.raw + "05")
		out, err := s.Decode(payload)
		if err != nil {
			t.Fatalf("%s: %v", c.label, err)
		}
		if _, present := out["v"]; present {
			t.Errorf("%s: v = %v, want absent", c.label, out["v"])
		}
		if mustNum(out["w"]) != 5 {
			t.Errorf("%s: w = %v, want 5", c.label, out["w"])
		}
	}
}
