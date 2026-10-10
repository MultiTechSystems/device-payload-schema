package schema

import (
	"strings"
	"testing"
)

// CR-2026-112 (PS-532): a key template writes a number as PS-406 does, a string as it
// is and a boolean as true or false; it refuses an object or repeat reference.

func TestRenderTemplateValue(t *testing.T) {
	cases := []struct {
		in   any
		want string
	}{
		{true, "true"}, {false, "false"}, {2.0, "2"}, {2.5, "2.5"}, {1e-7, "1e-7"},
		{1.5e21, "1.5e+21"}, {int64(-3), "-3"}, {"boost", "boost"},
	}
	for _, c := range cases {
		got, err := renderTemplateValue(c.in, "r", "k_${r}")
		if err != nil || got != c.want {
			t.Errorf("renderTemplateValue(%#v) = %q, %v; want %q", c.in, got, err, c.want)
		}
	}
	if _, err := renderTemplateValue(map[string]any{"a": 1}, "r", "k_${r}"); err == nil {
		t.Error("an object reference must be an error")
	}
}

func TestNameFromObjectReferenceRejectedAtLoad(t *testing.T) {
	for _, kind := range []string{"object", "repeat\n    count: 1"} {
		_, err := ParseSchema("name: bad\nfields:\n  - name: grp\n    type: " + kind +
			"\n    fields:\n      - {name: a, type: u8}\n  - {name: v, type: u8, name_from: \"v_${grp}\"}\n")
		if err == nil || !strings.Contains(err.Error(), "PS-532") {
			t.Errorf("%s: expected a PS-532 load error, got %v", kind, err)
		}
	}
}

func TestNameFromEncodeRendersBooleanAndLabel(t *testing.T) {
	s, err := ParseSchema(`
name: enc
endian: big
fields:
  - name: side
    type: u8
    var: at
    lookup: {1: left, 2: right}
  - {name: wet, type: bool, bit: 0, consume: 1}
  - {name: reading, type: u8, name_from: "r_${at}_${wet}"}
`)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	out, err := s.Decode([]byte{0x02, 0x01, 0x05})
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := out["r_right_true"]; !ok {
		t.Fatalf("decode = %#v, want r_right_true", out)
	}
	payload, err := s.Encode(out)
	if err != nil || string(payload) != "\x02\x01\x05" {
		t.Errorf("encode = % x, %v; want 02 01 05", payload, err)
	}
}
