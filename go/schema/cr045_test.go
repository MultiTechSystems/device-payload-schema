// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"strings"
	"testing"
)

// CR-2026-045 and CR-2026-051: references (PS-345 to PS-349, PS-461, PS-462) and
// literals (PS-357 to PS-361).

func TestCR045InvalidReferencesAreRejected(t *testing.T) {
	cases := map[string]string{
		"PS-345": "definitions:\n  d: [{name: v, type: u8}]\nfields:\n  - $ref: '#/definitions/d'\n",
		"PS-348": "definitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n  - $ref: '#/definitions/missing'\n",
		"PS-462": "definitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n  - $ref: 'lib.yaml#/definitions/d'\n",
		"PS-461": "definitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n  - $ref: '#/d'\n",
		"PS-349": "definitions:\n  a: {fields: [{$ref: '#/definitions/b'}]}\n  b: {fields: [{$ref: '#/definitions/a'}]}\nfields:\n  - {name: x, type: u8}\n",
	}
	for tag, body := range cases {
		if _, err := ParseSchema("name: p\n" + body); err == nil || !strings.Contains(err.Error(), tag) {
			t.Errorf("%s: error %v", tag, err)
		}
	}
}

func TestCR045AReferenceResolvesInANestedList(t *testing.T) {
	out, err := decodeHex(t, "name: p\ndefinitions:\n  d: {fields: [{name: v, type: u8}]}\nfields:\n"+
		"  - {name: o, type: object, fields: [{$ref: '#/definitions/d'}]}\n"+
		"  - {name: r, type: repeat, count: 2, fields: [{$ref: '#/definitions/d'}]}\n", "010203")
	if err != nil {
		t.Fatal(err)
	}
	if mustNum(out["o"].(map[string]any)["v"]) != 1 || len(out["r"].([]any)) != 2 {
		t.Errorf("out = %v", out)
	}
}

func TestCR051Literals(t *testing.T) {
	for _, field := range []string{
		"{name: k, type: string, value: 3}",
		"{name: k, type: number, value: '3'}",
		"{name: k, type: number, value: 3, mult: 2}",
	} {
		if _, err := ParseSchema("name: p\nfields:\n  - " + field + "\n"); err == nil {
			t.Errorf("%s: accepted (PS-358)", field)
		}
	}
	if _, err := decodeHex(t, "name: p\nfields:\n  - {name: s, type: string, length: 2}\n", "6162"); err == nil ||
		!strings.Contains(err.Error(), "PS-361") {
		t.Errorf("string with no value: %v", err)
	}
	s, err := ParseSchema("name: p\nfields:\n  - {name: cid, type: u8, value: 16}\n  - {name: unit, type: string, value: ppm}\n  - {name: x, type: u8}\n")
	if err != nil {
		t.Fatal(err)
	}
	result, err := s.EncodeToResult(map[string]any{"cid": 153, "x": 5}, 0)
	if err != nil {
		t.Fatal(err)
	}
	if string(result.Payload) != "\x10\x05" || len(result.Warnings) != 0 {
		t.Errorf("encode = % x %v, want 10 05 and no warnings (PS-359, PS-360)", result.Payload, result.Warnings)
	}
}
