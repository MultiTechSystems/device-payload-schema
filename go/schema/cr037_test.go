// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// CR-2026-037: the type vocabulary is closed. An unknown or absent type is rejected when
// the schema is loaded (PS-327, PS-328, PS-334); the PS-049 alias table is exhaustive
// (PS-326) and case-sensitive (PS-333); udec and sdec are required (PS-329, PS-330).

func oneField(typ string) string {
	return "name: p\nfields:\n  - name: v\n    type: '" + typ + "'\n"
}

func TestCR037UndefinedSpellingsAreRejected(t *testing.T) {
	for _, spelling := range []string{"float", "double", "UDec", "U8", "Byte", "UInt", "SInt",
		"BInt", "Float16", "Bits", "TLV", "Switch", "CTRL-SWITCH", "version_string",
		"hex:upper", "tlv"} {
		_, err := ParseSchema(oneField(spelling))
		if err == nil || !strings.Contains(err.Error(), "(v)") || !strings.Contains(err.Error(), spelling) {
			t.Errorf("%s: error %v, want one naming the field and the spelling", spelling, err)
		}
	}
}

func TestCR037AFieldWithNoTypeIsRejected(t *testing.T) {
	_, err := ParseSchema("name: p\nfields:\n  - name: v\n")
	if err == nil || !strings.Contains(err.Error(), "declares no type") {
		t.Fatalf("error %v, want declares no type", err)
	}
}

func TestCR037EveryAliasDecodes(t *testing.T) {
	cases := []struct {
		typ, payload string
		want         float64
	}{
		{"uint8", "07", 7}, {"uint64", "0000000000000009", 9}, {"int64", "FFFFFFFFFFFFFFF6", -10},
		{"i24", "FFFFFB", -5}, {"int8", "FE", -2}, {"udec", "25", 2.5}, {"sdec", "E5", -1.5},
	}
	for _, c := range cases {
		s, err := ParseSchema(oneField(c.typ))
		if err != nil {
			t.Fatalf("%s: %v", c.typ, err)
		}
		data, _ := hex.DecodeString(c.payload)
		out, err := s.Decode(data)
		if err != nil {
			t.Fatalf("%s: %v", c.typ, err)
		}
		if got := mustNum(out["v"]); got != c.want {
			t.Errorf("%s: got %v, want %v", c.typ, got, c.want)
		}
	}
}

func TestCR037IntegerReportsAnIntegerAndRejectsAFraction(t *testing.T) {
	s, err := ParseSchema("name: p\nfields:\n  - name: a\n    type: u8\n" +
		"  - name: half\n    type: integer\n    compute: {op: div, a: $a, b: 2}\n")
	if err != nil {
		t.Fatal(err)
	}
	out, err := s.Decode([]byte{8})
	if err != nil {
		t.Fatal(err)
	}
	if v, ok := out["half"].(int64); !ok || v != 4 {
		t.Errorf("half = %#v, want int64 4", out["half"])
	}
	if _, err := s.Decode([]byte{7}); err == nil {
		t.Error("a fractional integer must be an error (PS-388)")
	}
}

func TestCR037TheVocabularyFixtureRoundTrips(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "schemas", "devices",
		"_language-conformance", "type-vocabulary.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	s, err := ParseSchema(string(raw))
	if err != nil {
		t.Fatal(err)
	}
	want := "010203040506" + "0708090a" + "0000000000000001" + "fffefffdfffcfffffbfffffa" +
		"fffffff9fffffff8" + "fffffffffffffff7fffffffffffffff6fffffffffffffff5" + "3c0025e5414243"
	payload, _ := hex.DecodeString(want)
	out, err := s.Decode(payload)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := s.Encode(out)
	if err != nil {
		t.Fatal(err)
	}
	if got := hex.EncodeToString(encoded); got != want {
		t.Errorf("re-encoded\n %s\nwant\n %s", got, want)
	}
}
