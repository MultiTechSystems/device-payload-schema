// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"strings"
	"testing"
)

// CR-2026-107: a boolean literal (PS-357, PS-358 amended; PS-519, PS-520).

func TestCR107BooleanLiteralIsRefusedWhenMalformed(t *testing.T) {
	cases := map[string]string{
		"{name: k, type: boolean}":                              "PS-519",
		"{name: k, type: boolean, value: yes}":                  "PS-520",
		"{name: k, type: boolean, value: on}":                   "PS-520",
		`{name: k, type: boolean, value: "true"}`:               "PS-520",
		"{name: k, type: boolean, value: 1}":                    "PS-520",
		"{name: k, type: boolean, value: true, lookup: {1: x}}": "PS-358",
	}
	for field, tag := range cases {
		if _, err := ParseSchema("name: p\nfields:\n  - " + field + "\n"); err == nil ||
			!strings.Contains(err.Error(), tag) {
			t.Errorf("%s: error %v, want %s", field, err, tag)
		}
	}
}

func TestCR107BooleanLiteralInEveryFieldList(t *testing.T) {
	cases := []struct{ name, body, hex string }{
		{"top", "  - {name: a, type: u8}\n  - {name: hw, type: boolean, value: true}\n  - {name: b, type: u8}\n", "0105"},
		{"match", "  - {name: kind, type: u8}\n  - match:\n      field: $kind\n      cases:\n        1:\n" +
			"          - {name: hw, type: boolean, value: true}\n          - {name: b, type: u8}\n", "0105"},
		{"tlv", "  - tlv:\n      tag_size: 1\n      cases:\n        1:\n" +
			"          - {name: hw, type: boolean, value: true}\n          - {name: b, type: u8}\n", "0105"},
		{"object", "  - name: o\n    type: object\n    fields:\n      - {name: hw, type: boolean, value: true}\n      - {name: b, type: u8}\n", "05"},
		{"repeat", "  - name: r\n    type: repeat\n    count: 2\n    fields:\n      - {name: hw, type: boolean, value: true}\n      - {name: b, type: u8}\n", "0506"},
	}
	for _, c := range cases {
		text := "name: p\nfields:\n" + c.body
		out, err := decodeHex(t, text, c.hex)
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		var hw any
		switch c.name {
		case "object":
			hw = out["o"].(map[string]any)["hw"]
		case "repeat":
			hw = out["r"].([]any)[1].(map[string]any)["hw"]
		default:
			hw = out["hw"]
		}
		if hw != true {
			t.Errorf("%s: hw = %#v, want true (out %v)", c.name, hw, out)
		}
		s, err := ParseSchema(text)
		if err != nil {
			t.Fatal(err)
		}
		result, err := s.EncodeToResult(out, 0)
		if err != nil {
			t.Fatalf("%s: encode: %v", c.name, err)
		}
		if got := strings.ToLower(hexOf(result.Payload)); got != c.hex {
			t.Errorf("%s: encode = %s, want %s (PS-359)", c.name, got, c.hex)
		}
	}
}

func hexOf(b []byte) string {
	const digits = "0123456789abcdef"
	out := make([]byte, 0, len(b)*2)
	for _, x := range b {
		out = append(out, digits[x>>4], digits[x&15])
	}
	return string(out)
}
