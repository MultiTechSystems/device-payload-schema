// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"encoding/hex"
	"strings"
	"testing"
)

// CR-2026-058: what the specification described and never required. The fixtures in
// _language-conformance cover what a vector can express; these cover the rejections,
// errors and absent keys a vector cannot.

func TestCR058RejectedAtLoad(t *testing.T) {
	cases := map[string]string{
		"round key":     "  - {name: v, type: u8, transform: [{round: 1}]}\n",
		"op floor":      "  - {name: v, type: u8, transform: [{op: floor}]}\n",
		"bad ties":      "  - {name: v, type: u8, transform: [{op: round, ties: up}]}\n",
		"sub stage":     "  - {name: v, type: u8, transform: [{sub: 3}]}\n",
		"sep on base64": "  - {name: v, type: bytes, length: 2, format: base64, separator: ':'}\n",
		"hex:lower":     "  - {name: v, type: bytes, length: 2, format: 'hex:lower'}\n",
		"overlap":       "  - byte_group: {size: 1, fields: [{name: a, type: 'u8[0:3]'}, {name: b, type: 'u8[2:5]'}]}\n",
		"both sources":  "  - {name: k, type: u8, var: k}\n  - match: {field: $k, length: 1, cases: {1: [{name: a, type: u8}]}}\n",
		"no source":     "  - match: {cases: {1: [{name: a, type: u8}]}}\n",
	}
	for label, fields := range cases {
		if _, err := ParseSchema("name: p\nfields:\n" + fields); err == nil {
			t.Errorf("%s: accepted", label)
		}
	}
}

func decodeHex(t *testing.T, yaml, payload string) (map[string]any, error) {
	t.Helper()
	s, err := ParseSchema(yaml)
	if err != nil {
		t.Fatalf("ParseSchema: %v", err)
	}
	data, _ := hex.DecodeString(payload)
	return s.Decode(data)
}

func TestCR058RoundTies(t *testing.T) {
	for _, c := range []struct {
		ties     string
		h, n     float64
	}{{"even", 78.12, -2}, {"away", 78.13, -3}} {
		out, err := decodeHex(t, "name: p\nfields:\n"+
			"  - {name: h, type: u8, transform: [{mult: 100}, {div: 256}, {op: round, decimals: 2, ties: "+c.ties+"}]}\n"+
			"  - {name: n, type: s8, transform: [{div: 2}, {op: round, ties: "+c.ties+"}]}\n", "C8FB")
		if err != nil {
			t.Fatal(err)
		}
		if mustNum(out["h"]) != c.h || mustNum(out["n"]) != c.n {
			t.Errorf("ties %s: h=%v n=%v, want %v %v", c.ties, out["h"], out["n"], c.h, c.n)
		}
	}
}

func TestCR058ExceedingMaxIsAnError(t *testing.T) {
	for bound, unparsed := range map[string]string{
		"count: $n":      "4 byte(s) at offset 1",
		"until: end":     "2 byte(s) at offset 3",
		"byte_length: 4": "2 byte(s) at offset 3",
	} {
		_, err := decodeHex(t, "name: p\nfields:\n  - {name: n, type: u8, var: n}\n"+
			"  - {name: r, type: repeat, max: 2, "+bound+", fields: [{name: v, type: u8}]}\n", "040A141E28")
		if err == nil || !strings.Contains(err.Error(), "max of 2") || !strings.Contains(err.Error(), unparsed) {
			t.Errorf("%s: error %v", bound, err)
		}
	}
}

func TestCR058AFailedGuardWithoutElseOmitsTheField(t *testing.T) {
	out, err := decodeHex(t, "name: p\nfields:\n  - {name: raw, type: u8}\n"+
		"  - {name: scaled, type: number, ref: $raw, mult: 2, guard: {when: [{field: $raw, lt: 100}]}}\n"+
		"  - {name: after, type: u8}\n", "C805")
	if err != nil {
		t.Fatal(err)
	}
	if v, present := out["scaled"]; present {
		t.Errorf("scaled = %v, want absent", v)
	}
	if mustNum(out["after"]) != 5 {
		t.Errorf("after = %v", out["after"])
	}
}

func TestCR058TheEnumDescriptionFormReportsItsName(t *testing.T) {
	out, err := decodeHex(t, "name: p\nfields:\n  - name: m\n    type: enum\n    values:\n"+
		"      1: {name: standby, description: radio off}\n", "01")
	if err != nil {
		t.Fatal(err)
	}
	if out["m"] != "standby" {
		t.Errorf("m = %#v, want standby", out["m"])
	}
}
