package schema

import (
	"strings"
	"testing"
)

// CR-2026-133 (PS-550): an encoder given a repeat whose identity, unit or ipso.instance
// names a sibling repeat rejects arrays of different lengths, naming both, as a decoder
// does (PS-546). Mirrors tests/test_cr_2026_133_sibling_length_encode.py.

func TestCR133EqualLengthsEncode(t *testing.T) {
	s := cr111Parse(t, cr123Codes+cr123Values("2", ""))
	got, err := cr121Encode(t, s, map[string]any{"codes": []any{"1.8.1", "2.8.1"}, "values": []any{123, 456}})
	if err != nil || got != "01020000007b000001c8" {
		t.Errorf("encode = %s, %v", got, err)
	}
}

func TestCR133DifferentLengthsAreRejected(t *testing.T) {
	s := cr111Parse(t, cr123Codes+cr123Values("2", ""))
	for _, c := range []struct {
		values []any
		want   string
	}{
		{[]any{123, 456, 789}, "Repeat 'values' is given 3 values and 'codes', which identifies them, 2 (PS-550)"},
		{[]any{123}, "Repeat 'values' is given 1 values and 'codes', which identifies them, 2 (PS-550)"},
	} {
		_, err := s.Encode(map[string]any{"codes": []any{"1.8.1", "2.8.1"}, "values": c.values})
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("got %v, want %s", err, c.want)
		}
	}
}

func TestCR133SiblingUnitIsHeldToIt(t *testing.T) {
	s := cr111Parse(t, "  - {name: units, type: repeat, count: 2, element: {type: u8, lookup: {1: Wh, 2: W}}}\n"+
		"  - {name: values, type: repeat, count: 2, index: i, element: {type: u32, unit: '$units[i]'}}\n")
	_, err := s.Encode(map[string]any{"units": []any{"Wh", "W"}, "values": []any{1}})
	want := "Repeat 'values' is given 1 values and 'units', which identifies them, 2 (PS-550)"
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Errorf("got %v, want %s", err, want)
	}
}
