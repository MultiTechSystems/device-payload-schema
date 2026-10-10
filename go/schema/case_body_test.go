// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"strings"
	"testing"
)

// PS-347, PS-441: a `match` or `tlv` case body is a field list, `[]` for a case that
// reads nothing. A bare string such as `5: skip` was parsed as an empty case, so a match
// decoded nothing for that value and reported success, and a tlv dropped the case and
// skipped the tag. Mirrors tests/test_case_body_field_list.py.

func TestACaseBodyThatIsNotAFieldListIsRejectedAtLoad(t *testing.T) {
	cases := []struct{ label, schema, want string }{
		{"match", "name: p\nfields:\n- match:\n    length: 1\n    cases:\n      5: skip\n      6: [{name: a, type: u8}]\n",
			"match.cases[5]: a case body is a field list; write [] for a case that reads nothing (PS-441)"},
		{"tlv", "name: p\nfields:\n- tlv:\n    tag_size: 1\n    cases:\n      1: skip\n",
			"tlv.cases[1]: a case body is a field list; write [] for a case that reads nothing (PS-441)"},
		{"inside a nested group", "name: p\nfields:\n- name: g\n  type: object\n  fields:\n  - match:\n      length: 1\n      cases:\n        5: skip\n",
			"match.cases[5]: a case body is a field list; write [] for a case that reads nothing (PS-441)"},
	}
	for _, c := range cases {
		_, err := ParseSchema(c.schema)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: want an error containing %q, got %v", c.label, c.want, err)
		}
	}
}

func TestAnEmptyCaseBodyIsAccepted(t *testing.T) {
	s, err := ParseSchema("name: p\nfields:\n- match:\n    length: 1\n    cases:\n      5: []\n      6: [{name: a, type: u8}]\n")
	if err != nil {
		t.Fatalf("an empty field list is a valid case body: %v", err)
	}
	out, err := s.Decode([]byte{5})
	if err != nil || len(out) != 0 {
		t.Errorf("want an empty decode, got %v, %v", out, err)
	}
}
