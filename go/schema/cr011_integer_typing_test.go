package schema

import (
	"encoding/hex"
	"testing"
)

// CR-2026-011: an integer-typed field is reported through an integer channel with its
// exact value.
//
// Every numeric field used to arrive as a float64 whatever the declared type said, so a
// u64 of 2^64-1 came back as 1.8446744073709552e+19 and a u16 of 60 as a float64 where
// the clause 1 table says integer. Only Python was exact; this decoder was one of the six
// that were not.

func cr011Decode(t *testing.T, declared, payload string) any {
	t.Helper()
	s, err := ParseSchema("name: t\nendian: big\nfields:\n  - {name: v, " + declared + "}\n")
	if err != nil {
		t.Fatalf("ParseSchema: %v", err)
	}
	raw, err := hex.DecodeString(payload)
	if err != nil {
		t.Fatalf("bad payload %q: %v", payload, err)
	}
	out, err := s.Decode(raw)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	return out["v"]
}

func TestCR2026011IntegerWidthsReportThroughAnIntegerChannel(t *testing.T) {
	// PS-293.
	unsigned := map[string]string{
		"type: u8": "01", "type: u16": "0001", "type: u32": "00000001",
		"type: u64": "0000000000000001",
	}
	for declared, payload := range unsigned {
		switch v := cr011Decode(t, declared, payload).(type) {
		case uint64:
			if v != 1 {
				t.Errorf("%s = %d, want 1", declared, v)
			}
		default:
			t.Errorf("%s reported as %T, want an unsigned integer channel", declared, v)
		}
	}

	signed := map[string]string{
		"type: s8": "FF", "type: s16": "FFFF", "type: s32": "FFFFFFFF",
		"type: s64": "FFFFFFFFFFFFFFFF",
	}
	for declared, payload := range signed {
		v, ok := cr011Decode(t, declared, payload).(int64)
		if !ok {
			t.Errorf("%s did not report through a signed integer channel", declared)
		} else if v != -1 {
			t.Errorf("%s = %d, want -1", declared, v)
		}
	}
}

func TestCR2026011AModifierMakesTheFieldANumber(t *testing.T) {
	// PS-279: a field carrying div is a `number`, not an integer.
	if v, ok := cr011Decode(t, "type: s16, div: 10", "00EB").(float64); !ok || v != 23.5 {
		t.Errorf("scaled field = %v (%T), want 23.5 as float64", v, v)
	}
}

func TestCR2026011SixtyFourBitValuesAreExact(t *testing.T) {
	// PS-294.
	if v, ok := cr011Decode(t, "type: u64", "FFFFFFFFFFFFFFFF").(uint64); !ok || v != ^uint64(0) {
		t.Errorf("u64 max = %v, want 18446744073709551615", v)
	}
	if v, ok := cr011Decode(t, "type: u64", "0020000000000001").(uint64); !ok || v != (uint64(1)<<53)+1 {
		t.Errorf("u64 2^53+1 = %v, want 9007199254740993", v)
	}
	if v, ok := cr011Decode(t, "type: s64", "8000000000000000").(int64); !ok || v != -(int64(1)<<62)*2 {
		t.Errorf("s64 min = %v, want -9223372036854775808", v)
	}
}

func TestCR2026011AnUnsignedFieldIsNeverNegative(t *testing.T) {
	// PS-295 forbids a sign-changed value: u64 maps to uint64, and two implementations
	// reported -1 for this payload.
	v, ok := cr011Decode(t, "type: u64", "FFFFFFFFFFFFFFFF").(uint64)
	if !ok {
		t.Fatalf("u64 max did not report as uint64")
	}
	if v == 0 {
		t.Error("u64 max reported as zero")
	}
}
