// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"strings"
	"testing"
)

// CR-2026-044: a ragged tail under `until: end` is an error naming the repeat (PS-343,
// PS-344), tested before an element of fixed size begins (PS-344a).
func TestCR044ARaggedTailIsAnError(t *testing.T) {
	s, err := ParseSchema("name: p\nfields:\n  - {name: r, type: repeat, until: end, fields: [{name: a, type: u8}, {name: b, type: u16}]}\n")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Decode([]byte{1, 0, 2, 2}); err == nil ||
		!strings.Contains(err.Error(), "'r' ends in a ragged tail") ||
		!strings.Contains(err.Error(), "fewer than the 3") {
		t.Errorf("error = %v", err)
	}
	if out, err := s.Decode([]byte{1, 0, 2, 2, 0, 3}); err != nil || len(out["r"].([]any)) != 2 {
		t.Errorf("whole elements: %v %v", out, err)
	}
}
