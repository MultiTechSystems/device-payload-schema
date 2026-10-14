// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"strings"
	"testing"
)

// PS-398, PS-151: a range case key has numeric bounds, not necessarily integers, and a
// malformed one is rejected at load. Mirrors tests/test_range_case_keys.py.

func rangeKeySchema(key string) string {
	return fmt.Sprintf("name: p\nfields:\n- name: v\n  type: u8\n- match:\n    field: $v\n    default: []\n    cases:\n      '%s':\n        - {name: hit, type: u8}\n", key)
}

func TestAMalformedRangeKeyIsRejectedAtLoad(t *testing.T) {
	for _, key := range []string{"a..b", "1..", "..2", "1...5", "1.5.5..9", "5..2", "9..1.5"} {
		_, err := ParseSchema(rangeKeySchema(key))
		want := "match.cases[" + key + "]: "
		if err == nil || !strings.Contains(err.Error(), want) || !strings.Contains(err.Error(), "PS-398") {
			t.Errorf("%s: want an error containing %q and PS-398, got %v", key, want, err)
		}
	}
}

func TestAnIntegerDiscriminatorMeetsFractionalBounds(t *testing.T) {
	s, err := ParseSchema(rangeKeySchema("2.5..7.5"))
	if err != nil {
		t.Fatal(err)
	}
	for v, want := range map[byte]bool{2: false, 3: true, 7: true, 8: false} {
		out, err := s.Decode([]byte{v, 9})
		if err != nil {
			t.Fatal(err)
		}
		if _, got := out["hit"]; got != want {
			t.Errorf("%d: hit present = %v, want %v", v, got, want)
		}
	}
}

func TestAHexadecimalAndNegativeBoundMatch(t *testing.T) {
	s, err := ParseSchema(rangeKeySchema("0x10..0x1F"))
	if err != nil {
		t.Fatal(err)
	}
	for v, want := range map[byte]bool{0x0F: false, 0x10: true, 0x1F: true, 0x20: false} {
		out, _ := s.Decode([]byte{v, 5})
		if _, got := out["hit"]; got != want {
			t.Errorf("%#x: hit present = %v, want %v", v, got, want)
		}
	}
	if _, _, ok := parseRangeKey("-10.5..-0.5"); !ok {
		t.Error("a negative fractional range key is a range key")
	}
}
