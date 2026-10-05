// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"strings"
	"testing"
)

// CR-2026-038, -041, -042: a ports schema decoded with no FPort is an error (PS-459); a
// port key is 1 to 255 (PS-018); a top-level fPort is a port, and every ports key beside
// it equals it (PS-335, PS-337), and it is never consulted when decoding (PS-336).

const portedYAML = "name: p\nports:\n  1:\n    fields: [{name: a, type: u8}]\n  default:\n    fields: [{name: b, type: u8}]\n"

func TestCR038NoFPortIsAnError(t *testing.T) {
	s, err := ParseSchema(portedYAML)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Decode([]byte{7}); err == nil || !strings.Contains(err.Error(), "no FPort was supplied") {
		t.Errorf("Decode() error = %v, want the PS-459 error", err)
	}
	out, err := s.DecodeWithPort([]byte{7}, 9)
	if err != nil || mustNum(out["b"]) != 7 {
		t.Errorf("an unmatched FPort still uses the default: %v %v", out, err)
	}
}

func TestCR041And042PortDeclarations(t *testing.T) {
	for label, yaml := range map[string]string{
		"port 0":          "name: p\nports:\n  0:\n    fields: [{name: a, type: u8}]\n",
		"port 256":        "name: p\nports:\n  256:\n    fields: [{name: a, type: u8}]\n",
		"fPort 0":         "name: p\nfPort: 0\nfields: [{name: a, type: u8}]\n",
		"fPort string":    "name: p\nfPort: '225'\nfields: [{name: a, type: u8}]\n",
		"fPort vs ports":  "name: p\nfPort: 20\nports:\n  21:\n    fields: [{name: a, type: u8}]\n",
	} {
		if _, err := ParseSchema(yaml); err == nil {
			t.Errorf("%s: accepted", label)
		}
	}
	s, err := ParseSchema("name: p\nfport: 225\nfields: [{name: a, type: u8}]\n")
	if err != nil {
		t.Fatal(err)
	}
	if out, err := s.Decode([]byte{5}); err != nil || mustNum(out["a"]) != 5 {
		t.Errorf("the top-level fPort is not consulted: %v %v", out, err)
	}
}
