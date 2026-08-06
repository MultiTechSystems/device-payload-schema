package schema

import "testing"

// CR-2026-004 (PS-265..PS-270). The sequence form of `lookup` was unparsed here,
// so every schema using it decoded a raw integer while the map form worked.
func TestSequenceLookupIsApplied(t *testing.T) {
	s, err := ParseSchema("name: seq\nfields:\n  - name: relay\n    type: u8\n    lookup: [\"off\", \"on\"]\n")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	out, err := s.Decode([]byte{0x01})
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out["relay"] != "on" {
		t.Errorf("relay = %#v, want \"on\"", out["relay"])
	}
}

func TestSequenceLookupOutOfRangeKeepsRawValue(t *testing.T) {
	s, _ := ParseSchema("name: seq\nfields:\n  - name: relay\n    type: u8\n    lookup: [\"off\", \"on\"]\n")
	out, _ := s.Decode([]byte{0x07})
	if v, ok := toInt(out["relay"]); !ok || v != 7 {
		t.Errorf("relay = %#v, want raw 7", out["relay"])
	}
}

func TestSparseMappingLookup(t *testing.T) {
	s, _ := ParseSchema("name: sparse\nfields:\n  - name: button\n    type: u8\n    lookup: {1: short, 2: long, 3: double}\n")
	for raw, want := range map[byte]string{1: "short", 2: "long", 3: "double"} {
		out, _ := s.Decode([]byte{raw})
		if out["button"] != want {
			t.Errorf("raw %d -> %#v, want %q", raw, out["button"], want)
		}
	}
}

func TestUnmappedValueOmitsField(t *testing.T) {
	s, _ := ParseSchema("name: sparse\nfields:\n  - name: button\n    type: u8\n    lookup: {1: short}\n")
	out, _ := s.Decode([]byte{0x09})
	if _, present := out["button"]; present {
		t.Errorf("button should be omitted, got %#v", out["button"])
	}
}

func TestLookupDefault(t *testing.T) {
	s, _ := ParseSchema("name: def\nfields:\n  - name: state\n    type: u8\n    lookup: {1: on, default: unknown}\n")
	out, _ := s.Decode([]byte{0x09})
	if out["state"] != "unknown" {
		t.Errorf("state = %#v, want \"unknown\"", out["state"])
	}
}

func TestNameFrom(t *testing.T) {
	s, err := ParseSchema(`
name: computed
endian: little
fields:
  - name: region_id
    type: u8
  - name: avg_dwell
    name_from: "region_${region_id}_avg_dwell"
    type: u16
`)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	out, err := s.Decode([]byte{0x03, 0x10, 0x00})
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := out["region_3_avg_dwell"]; !ok {
		t.Errorf("expected key region_3_avg_dwell, got %#v", out)
	}
}

func TestNameFromUnresolvedIsAnError(t *testing.T) {
	s, _ := ParseSchema("name: bad\nfields:\n  - name: v\n    type: u8\n    name_from: \"x_${nope}\"\n")
	if _, err := s.Decode([]byte{0x01}); err == nil {
		t.Error("expected an error for an unresolved name_from reference")
	}
}

func TestNegatedAndWildcardCaseKeys(t *testing.T) {
	s, err := ParseSchema(`
name: tags
endian: little
fields:
  - tlv:
      tag_fields:
        - name: channel_id
          type: u8
        - name: channel_type
          type: u8
      tag_key: [channel_id, channel_type]
      cases:
        "[1, 200]":
          - name: exact
            type: u8
        "[1, !0]":
          - name: any_but_zero
            type: u8
        "[2, *]":
          - name: any_type
            type: u8
`)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	for _, tc := range []struct {
		payload []byte
		key     string
	}{
		{[]byte{0x01, 0xc8, 0x07}, "exact"},
		{[]byte{0x01, 0x05, 0x01}, "any_but_zero"},
		{[]byte{0x02, 0x63, 0x0a}, "any_type"},
	} {
		out, err := s.Decode(tc.payload)
		if err != nil {
			t.Fatalf("decode %x: %v", tc.payload, err)
		}
		if _, ok := out[tc.key]; !ok {
			t.Errorf("payload %x: expected %q, got %#v", tc.payload, tc.key, out)
		}
	}
	out, _ := s.Decode([]byte{0x01, 0x00, 0x09})
	if _, ok := out["any_but_zero"]; ok {
		t.Errorf("type 0 must be excluded by [1, !0], got %#v", out)
	}
}
