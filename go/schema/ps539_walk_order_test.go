// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import "testing"

// PS-539 (CR-2026-127) checks references in one decode order: within a mapping,
// tag_fields, trailer, fields and element first, then the remaining keys in source order.
// Each schema below differs from its pair only in the order two keys are written, and
// that order alone decides whether the reference follows its declaration. Go walked
// sorted keys, scalars before nested values, and ports sorted as strings. Each port is a
// decode of its own (CR-2026-130), so a name another port declares is never in scope. Mirrors
// tests/test_ps539_walk_order.py, Java's ReferenceWalkOrderTest and C#'s
// ReferenceWalkOrderTests.

const ps539OrderMessage = "is referenced before the schema declares it (PS-539, PS-122)"

var ps539WalkOrderCases = []struct {
	label, doc string
	refused    string // "" when the schema loads
}{
	// A match's cases written before its discriminator declare the name it reads.
	{"cases before field",
		"fields:\n  - {name: k, type: u8}\n  - match: {cases: {1: [{name: x, type: u8}]}, default: skip, field: $x}\n",
		""},
	{"field before cases",
		"fields:\n  - {name: k, type: u8}\n  - match: {field: $x, default: skip, cases: {1: [{name: x, type: u8}]}}\n",
		"`$x` (field) " + ps539OrderMessage},
	// A match's default field list against its cases.
	{"default declares before cases read",
		"fields:\n  - {name: k, type: u8}\n  - match: {field: $k, default: [{name: x, type: u8}], cases: {1: [{name: y, type: number, ref: $x}]}}\n",
		""},
	{"default reads before cases declare",
		"fields:\n  - {name: k, type: u8}\n  - match: {field: $k, default: [{name: y, type: number, ref: $x}], cases: {1: [{name: x, type: u8}]}}\n",
		"`$x` (ref) " + ps539OrderMessage},
	// Each port is a decode of its own (CR-2026-130): a name only another port declares
	// is out of scope in either source order, and within a port the source order decides.
	{"port 10 reads what port 9, written first, declares",
		"ports:\n  9: {fields: [{name: x, type: u8}]}\n  10: {fields: [{name: y, type: number, ref: $x}]}\n",
		"`$x` (ref) " + ps539OrderMessage},
	{"port 2, written first, reads what port 1 declares",
		"ports:\n  2: {fields: [{name: y, type: number, ref: $x}]}\n  1: {fields: [{name: x, type: u8}]}\n",
		"`$x` (ref) " + ps539OrderMessage},
	{"each port declares its own",
		"ports:\n  9: {fields: [{name: x, type: u8}]}\n  10: {fields: [{name: x, type: u8}, {name: y, type: number, ref: $x}]}\n",
		""},
	{"within a port, declared before read",
		"ports:\n  10: {fields: [{name: x, type: u8}, {name: y, type: number, ref: $x}]}\n  9: {fields: [{name: z, type: u8}]}\n",
		""},
	{"within a port, read before declared",
		"ports:\n  10: {fields: [{name: y, type: number, ref: $x}, {name: x, type: u8}]}\n  9: {fields: [{name: z, type: u8}]}\n",
		"`$x` (ref) " + ps539OrderMessage},
	{"a metadata name is in scope in every port",
		"metadata: {include: [{name: eui, source: $device_eui}]}\n" +
			"ports:\n  1: {fields: [{name: a, type: number, ref: $eui}]}\n  2: {fields: [{name: b, type: number, ref: $eui}]}\n",
		""},
}

func TestPS539WalksInSourceOrder(t *testing.T) {
	// Repeated, so an order taken from map iteration could not pass by chance.
	for i := 0; i < 64; i++ {
		for _, c := range ps539WalkOrderCases {
			_, err := ParseSchema("name: t\n" + c.doc)
			switch {
			case c.refused == "" && err != nil:
				t.Errorf("%s (run %d): %v, want it to load", c.label, i, err)
			case c.refused != "" && (err == nil || err.Error() != c.refused):
				t.Errorf("%s (run %d): got %v, want %q", c.label, i, err, c.refused)
			}
		}
		if t.Failed() {
			return
		}
	}
}

// The same holds for a schema authored as JSON, whose key order the YAML node tree
// records as well.
func TestPS539WalksJSONInSourceOrder(t *testing.T) {
	ok := `{"name": "t", "fields": [{"name": "k", "type": "u8"}, {"match": ` +
		`{"cases": {"1": [{"name": "x", "type": "u8"}]}, "default": "skip", "field": "$x"}}]}`
	bad := `{"name": "t", "fields": [{"name": "k", "type": "u8"}, {"match": ` +
		`{"field": "$x", "default": "skip", "cases": {"1": [{"name": "x", "type": "u8"}]}}}]}`
	for i := 0; i < 64; i++ {
		if _, err := ParseSchema(ok); err != nil {
			t.Fatalf("run %d: %v, want it to load", i, err)
		}
		if _, err := ParseSchema(bad); err == nil || err.Error() != "`$x` (field) "+ps539OrderMessage {
			t.Fatalf("run %d: got %v", i, err)
		}
	}
}
