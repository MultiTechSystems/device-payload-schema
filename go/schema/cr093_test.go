// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"encoding/hex"
	"fmt"
	"reflect"
	"strings"
	"testing"
)

// CR-2026-093 (PS-486): a tlv entry is never partly read. A length cut short, or a length
// declaring more bytes than remain, is an error naming the tlv and the entry's offset,
// for a known tag and for an unknown one that would be skipped. Mirrors
// tests/test_cr_2026_093.py, whose cases are the CR's own.

const cr093Body = "      tag_size: 1\n      length_size: %d\n%s      cases:\n        1:\n          - {name: a, type: u8}\n        2:\n          - {name: b, type: bytes, length: remaining}\n"

func cr093Schema(lengthSize int, reserve string) string {
	return "name: probe\nfields:\n  - tlv:\n" + fmt.Sprintf(cr093Body, lengthSize, reserve)
}

func TestCR093ACutShortEntryIsAnError(t *testing.T) {
	cases := []struct {
		label, schema, payload, err string
	}{
		{"length byte missing", cr093Schema(1, ""), "01010702",
			"tlv entry at offset 3: 0 byte(s) remain after its tag, fewer than its 1-byte length (PS-486)"},
		{"value cut short", cr093Schema(1, ""), "0101070205aabb",
			"tlv entry at offset 3: its length declares 5 byte(s), 2 remain (PS-486)"},
		{"unknown tag skipped past the end", cr093Schema(1, ""), "0101070905aabb",
			"tlv entry at offset 3: its length declares 5 byte(s), 2 remain (PS-486)"},
		{"one of two length bytes", cr093Schema(2, ""), "010001070200",
			"tlv entry at offset 4: 1 byte(s) remain after its tag, fewer than its 2-byte length (PS-486)"},
		{"value cut short by a reserve", cr093Schema(1, "      reserve: 1\n"), "0101070205aabb",
			"tlv entry at offset 3: its length declares 5 byte(s), 1 remain (PS-486)"},
	}
	for _, c := range cases {
		s, err := ParseSchema(c.schema)
		if err != nil {
			t.Fatalf("%s: ParseSchema: %v", c.label, err)
		}
		data, _ := hex.DecodeString(c.payload)
		out, err := s.Decode(data)
		if err == nil || !strings.Contains(err.Error(), c.err) {
			t.Errorf("%s: got %v, %v; want error %q", c.label, out, err, c.err)
		}
	}
}

func TestCR093AnEntryThatFitsDecodes(t *testing.T) {
	for payload, want := range map[string]map[string]any{
		"010107":         {"a": 7},
		"0101070202aabb": {"a": 7, "b": "aabb"},
	} {
		s, err := ParseSchema(cr093Schema(1, ""))
		if err != nil {
			t.Fatal(err)
		}
		data, _ := hex.DecodeString(payload)
		out, err := s.Decode(data)
		if err != nil {
			t.Fatalf("%s: %v", payload, err)
		}
		got := map[string]any{}
		for k, v := range out {
			if k == "_warnings" {
				t.Errorf("%s: warnings %v", payload, v)
				continue
			}
			got[k] = fmt.Sprint(v)
		}
		wantText := map[string]any{}
		for k, v := range want {
			wantText[k] = fmt.Sprint(v)
		}
		if !reflect.DeepEqual(got, wantText) {
			t.Errorf("%s: got %v, want %v", payload, got, want)
		}
	}
}
