// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"reflect"
	"testing"
)

// CR-2026-117: a reference naming nothing declared is refused at load (PS-539), and a guard
// condition on an absent value fails, so its `else` applies (PS-540). Mirrors
// tests/test_cr_2026_117_references_and_guards.py; the message is the same text in all four.

const cr117Message = "`$batery` (ref) names no field, var, tag component, index or count_as " +
	"the schema declares (PS-539)"

func TestCR2026117UndeclaredReferenceIsRefused(t *testing.T) {
	_, err := ParseSchema("name: t\nfields:\n  - {name: battery, type: u8}\n" +
		"  - {name: x, type: number, ref: $batery}\n")
	if err == nil || err.Error() != cr117Message {
		t.Fatalf("got %v, want %q", err, cr117Message)
	}
	for _, field := range []string{
		"  - {name: x, type: u8, name_from: \"k_${batery}\"}\n",
		"  - match: {field: $batery, cases: {1: [{name: y, type: u8}]}}\n",
		"  - {name: x, type: number, ref: $battery, guard: {when: [{field: $batery, gt: 0}], else: 0}}\n",
		"  - {name: x, type: repeat, count: $batery, fields: [{name: y, type: u8}]}\n",
	} {
		_, err := ParseSchema("name: t\nfields:\n  - {name: battery, type: u8}\n" + field)
		expectErrorCiting(t, field, err, "PS-539")
	}
}

func TestCR2026117WhatCountsAsDeclared(t *testing.T) {
	for _, doc := range []string{
		// a var, a tag component, an index, count_as
		"fields:\n  - {name: raw, type: u8, var: kind}\n  - {name: n, type: number, ref: $kind}\n" +
			"  - tlv: {tag_fields: [{name: rsn, type: u8}, {name: ch, type: u8}], tag_key: ch, " +
			"cases: {1: [{name: r, type: number, ref: $rsn}]}}\n",
		"fields:\n  - {name: e, type: repeat, count: 1, index: i, count_as: k, fields: [{name: v, type: number, ref: $i}]}\n" +
			"  - {name: after, type: number, ref: $k}\n",
		// an unnamed element is decoded as `element` (PS-528)
		"fields:\n  - {name: v, type: repeat, count: 2, present_if: {field: $element, ne: 0}, element: {type: u8}}\n",
		// a metadata name counts, and its source is runtime input
		"fields:\n  - {name: a, type: u8}\n  - {name: b, type: number, ref: $eui}\n" +
			"metadata: {include: [{name: eui, source: $device_eui}]}\n",
		// data is not a reference
		"fields:\n  - {name: a, type: u8, lookup: {1: $nothing}}\n",
	} {
		if _, err := ParseSchema("name: t\n" + doc); err != nil {
			t.Errorf("%s: %v", doc, err)
		}
	}
}

func TestCR2026117GuardOnAnAbsentValue(t *testing.T) {
	base := "name: t\nfields:\n  - {name: a, type: u8}\n  - {name: d, type: u8}\n" +
		"  - {name: r, type: number, compute: {op: div, a: $a, b: $d}}\n"
	for _, c := range []struct {
		guard, payload string
		want           map[string]any
	}{
		{"{when: [{field: $r, gte: 0}], else: -1}", "0800", map[string]any{"a": uint64(8), "d": uint64(0), "c": -1}},
		{"{when: [{field: $r, ne: 3}], else: -1}", "0800", map[string]any{"a": uint64(8), "d": uint64(0), "c": -1}},
		{"{when: [{field: $r, gte: 0}]}", "0800", map[string]any{"a": uint64(8), "d": uint64(0)}},
	} {
		s, err := ParseSchema(base + "  - {name: c, type: number, ref: $a, guard: " + c.guard + "}\n")
		if err != nil {
			t.Fatal(err)
		}
		payload, _ := hexToBytes(c.payload)
		got, err := s.Decode(payload)
		if err != nil {
			t.Fatalf("%s: %v", c.guard, err)
		}
		if !numericMapEqual(got, c.want) {
			t.Errorf("%s: got %v, want %v", c.guard, got, c.want)
		}
	}
}

func numericMapEqual(got, want map[string]any) bool {
	if len(got) != len(want) {
		return false
	}
	for k, w := range want {
		g, ok := got[k]
		if !ok {
			return false
		}
		gf, gok := toFloat64(g)
		wf, wok := toFloat64(w)
		if gok && wok {
			if gf != wf {
				return false
			}
		} else if !reflect.DeepEqual(g, w) {
			return false
		}
	}
	return true
}
