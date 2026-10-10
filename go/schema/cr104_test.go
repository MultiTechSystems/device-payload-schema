package schema

// CR-2026-104: a lookup label may be a boolean (PS-106), and a boolean label matches only
// a boolean input (PS-513).
//
// Field.Lookup was a map[int]string, so the parser kept string labels only and a mapping
// with boolean labels parsed to an empty table: `{0: false, default: true}` decoded to no
// field at all, with success. Encoding then found no label and refused "true" as "not one
// of its declared values". Labels now hold any scalar, compared by sameLabel.

import (
	"encoding/hex"
	"strings"
	"testing"
)

const cr104Source = "name: t\nendian: big\nfields:\n" +
	"  - name: m\n    type: u8[4:7]\n    lookup: {0: false, default: true}\n" +
	"  - name: q\n    type: u8[0:0]\n    consume: 1\n    lookup: [true, false]\n"

func TestCR2026104DecodeReportsBooleans(t *testing.T) {
	s, err := ParseSchema(cr104Source)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	for payload, want := range map[string][2]bool{"31": {true, false}, "00": {false, true}} {
		raw, _ := hex.DecodeString(payload)
		got, err := s.Decode(raw)
		if err != nil {
			t.Fatalf("%s: %v", payload, err)
		}
		for i, name := range []string{"m", "q"} {
			value, present := got[name]
			if !present {
				t.Errorf("%s: %s missing from %v", payload, name, got)
				continue
			}
			if b, ok := value.(bool); !ok || b != want[i] {
				t.Errorf("%s: %s = %#v, want %v", payload, name, value, want[i])
			}
		}
	}
}

func TestCR2026104SequenceEncodesByIndex(t *testing.T) {
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: g\n    type: u8\n    lookup: [true, false]\n"
	for label, want := range map[bool]string{false: "01", true: "00"} {
		got, err := encodeHex(t, source, map[string]any{"g": label})
		if err != nil {
			t.Fatalf("%v: %v", label, err)
		}
		if got != want {
			t.Errorf("%v encoded as %q, want %q", label, got, want)
		}
	}
}

func TestCR2026104MappingEncodesByKey(t *testing.T) {
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: f\n    type: u8\n    lookup: {0: false, 1: true}\n"
	for label, want := range map[bool]string{false: "00", true: "01"} {
		got, err := encodeHex(t, source, map[string]any{"f": label})
		if err != nil {
			t.Fatalf("%v: %v", label, err)
		}
		if got != want {
			t.Errorf("%v encoded as %q, want %q", label, got, want)
		}
	}
}

func TestCR2026104BitRangesEncode(t *testing.T) {
	got, err := encodeHex(t, cr104Source, map[string]any{"m": false, "q": false})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if got != "01" {
		t.Errorf("encoded as %q, want %q", got, "01")
	}
}

func TestCR2026104DefaultOnlyBooleanIsRefused(t *testing.T) {
	// PS-409: `true` is carried only by the default, so no key stands behind it.
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: f\n    type: u8\n    lookup: {0: false, default: true}\n"
	if got, err := encodeHex(t, source, map[string]any{"f": false}); err != nil || got != "00" {
		t.Errorf("false: %q, %v", got, err)
	}
	got, err := encodeHex(t, source, map[string]any{"f": true})
	if err == nil {
		t.Fatalf("expected an error, got payload %q", got)
	}
	if !strings.Contains(err.Error(), "PS-513") {
		t.Errorf("error does not cite PS-513: %v", err)
	}
}

func TestCR2026104TrueIsNotOne(t *testing.T) {
	// PS-513: neither `true` against a label of 1, nor "true" against a label of true.
	numbers := "name: t\nendian: big\nfields:\n" +
		"  - name: n\n    type: u8\n    lookup: [0, 1]\n"
	if got, err := encodeHex(t, numbers, map[string]any{"n": true}); err == nil {
		t.Errorf("true encoded through [0, 1] as %q", got)
	}
	booleans := "name: t\nendian: big\nfields:\n" +
		"  - name: n\n    type: u8\n    lookup: {0: false, 1: true}\n"
	if got, err := encodeHex(t, booleans, map[string]any{"n": "true"}); err == nil {
		t.Errorf(`"true" encoded through {0: false, 1: true} as %q`, got)
	}
	if !sameLabel(true, true) || sameLabel(true, 1) || sameLabel(1.0, true) ||
		sameLabel("true", true) || !sameLabel(1, 1.0) {
		t.Errorf("sameLabel does not follow PS-513")
	}
}

func TestCR2026104TemplateRejectsBooleanLabels(t *testing.T) {
	// PS-407: a ${value} default reports a string, so a boolean label is a schema error.
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: f\n    type: u8\n    lookup: {0: false, default: \"v${value}\"}\n"
	if _, err := ParseSchema(source); err == nil || !strings.Contains(err.Error(), "PS-407") {
		t.Errorf("expected a PS-407 error, got %v", err)
	}
}

func TestCR2026104NumberLabels(t *testing.T) {
	// PS-106 has always allowed number labels; Field.Lookup kept strings only, so a
	// mapping of numbers - Netvox r718n3's multiplier - decoded to no field at all.
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: n\n    type: u8\n    lookup: {1: 1, 2: 5, 3: 10, 4: 100}\n"
	s, err := ParseSchema(source)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	got, err := s.Decode([]byte{2})
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if v, ok := toFloat64(got["n"]); !ok || v != 5 {
		t.Errorf("decoded %#v, want the number 5", got["n"])
	}
	if out, err := encodeHex(t, source, map[string]any{"n": 5}); err != nil || out != "02" {
		t.Errorf("5 encoded as %q, %v; want 02", out, err)
	}
	if out, err := encodeHex(t, source, map[string]any{"n": "5"}); err == nil {
		t.Errorf(`"5" encoded as %q; it is no label (PS-513)`, out)
	}
}
