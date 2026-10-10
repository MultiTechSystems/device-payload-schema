// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import "testing"

// A skip that runs past the end of the payload is a short read like any other. Python
// and the generated codec decoded it with success; this pins that Go does not.
func TestASkipPastTheEndIsAnError(t *testing.T) {
	s, err := ParseSchema("name: p\nfields:\n- {name: a, type: u8}\n- {name: pad, type: skip, length: 4}\n")
	if err != nil {
		t.Fatal(err)
	}
	if out, err := s.Decode([]byte{1, 2}); err == nil {
		t.Errorf("want a short-read error, got %v", out)
	}
}
