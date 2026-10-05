// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import "testing"

// CR-2026-039: byte order is declared with `endian`, never in a type name. Go never read
// the le_/be_ prefixes; this holds that they stay rejected (PS-053a).
func TestCR039APrefixedTypeIsRejected(t *testing.T) {
	for _, spelling := range []string{"le_u16", "be_u16", "le_f32", "le_u8[0:3]"} {
		if _, err := ParseSchema(oneField(spelling)); err == nil {
			t.Errorf("%s: accepted", spelling)
		}
	}
}
