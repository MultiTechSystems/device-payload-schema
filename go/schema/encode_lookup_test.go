package schema

// Reversing a `lookup` for encoding, both spellings of it.
//
// The corpus floor in corpus_encode_test.go catches a regression here in aggregate; these
// name the requirement, because the way this failed is worth keeping a test for. A
// sequence lookup (PS-104) parses into Field.LookupArray, and neither encode-side reversal
// consulted it - only Field.LookupDefault's mapping form. So every label a sequence
// produced stayed a string, failed the numeric conversion in encodeField, and the integer
// cases there wrote *nothing at all* when that conversion failed. 67 corpus vectors
// emitted a TLV tag followed by no value and reported success.
//
// Two properties are therefore tested separately: that the label comes back, and that a
// value the encoder cannot write is reported rather than skipped.

import (
	"encoding/hex"
	"strings"
	"testing"
)

func encodeHex(t *testing.T, source string, data map[string]any) (string, error) {
	t.Helper()
	s, err := ParseSchema(source)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	out, err := s.Encode(data)
	return hex.EncodeToString(out), err
}

func TestEncodeReversesASequenceLookup(t *testing.T) {
	// `lookup: [close, open]` - indexed from zero (PS-104).
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: door\n    type: u8\n    lookup: [close, open]\n"

	for label, want := range map[string]string{"close": "00", "open": "01"} {
		got, err := encodeHex(t, source, map[string]any{"door": label})
		if err != nil {
			t.Fatalf("%q: %v", label, err)
		}
		if got != want {
			t.Errorf("%q encoded as %q, want %q", label, got, want)
		}
	}
}

func TestEncodeReversesAMappingLookup(t *testing.T) {
	// `lookup: {1: short, 2: long}` - keys need not start at zero (PS-268).
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: button\n    type: u8\n    lookup:\n      1: short\n      2: long\n"

	got, err := encodeHex(t, source, map[string]any{"button": "long"})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if got != "02" {
		t.Errorf("encoded as %q, want %q", got, "02")
	}
}

func TestEncodeRefusesALabelWithNoValueBehindIt(t *testing.T) {
	// A `default` label stands for every value the table does not list (PS-269), so
	// there is no original to recover. Writing a plausible byte would be worse.
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: mode\n    type: u8\n    lookup:\n      0: idle\n      default: unknown\n"

	got, err := encodeHex(t, source, map[string]any{"mode": "unknown"})
	if err == nil {
		t.Fatalf("expected an error, got payload %q", got)
	}
	if !strings.Contains(err.Error(), "cannot be recovered") {
		t.Errorf("error does not say why: %v", err)
	}
}

func TestEncodeReportsAValueItCannotWrite(t *testing.T) {
	// The defect that hid the one above: a value the numeric cases could not convert
	// was skipped, so the field contributed no bytes and the call still succeeded.
	source := "name: t\nendian: big\nfields:\n  - name: count\n    type: u16\n"

	got, err := encodeHex(t, source, map[string]any{"count": "not a number"})
	if err == nil {
		t.Fatalf("expected an error, got payload %q", got)
	}
	if got != "" {
		t.Errorf("wrote %q for a value it could not encode", got)
	}
}

func TestEncodeWritesThe24BitWidths(t *testing.T) {
	// u24 and s24 are in decodeField's two integer cases and were in neither of
	// encodeField's, so they wrote nothing: oyster's port 4 re-encoded its latitude and
	// longitude as no bytes at all and kept the three fields after them.
	source := "name: t\nendian: little\nfields:\n" +
		"  - name: lat\n    type: s24\n  - name: count\n    type: u24\n"

	got, err := encodeHex(t, source, map[string]any{"lat": -390625.0, "count": 66051.0})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	// -390625 is 0xFA0A1F as a 24-bit two's complement value, little-endian.
	if got != "1f0afa030201" {
		t.Errorf("encoded as %q, want %q", got, "1f0afa030201")
	}
}

func TestEncodeSplicesARef(t *testing.T) {
	// Decoding splices a `$ref`'s fields in place; encoding did not, so a referenced
	// header contributed no bytes.
	source := "name: t\nendian: big\n" +
		"definitions:\n" +
		"  header:\n" +
		"    fields:\n" +
		"      - name: version\n        type: u8\n" +
		"      - name: kind\n        type: u8\n" +
		"fields:\n" +
		"  - $ref: '#/definitions/header'\n" +
		"  - name: value\n    type: u8\n"

	got, err := encodeHex(t, source, map[string]any{
		"version": 1.0, "kind": 2.0, "value": 3.0})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if got != "010203" {
		t.Errorf("encoded as %q, want %q", got, "010203")
	}
}

func TestEncodeRejectsATypeItHasNoCaseFor(t *testing.T) {
	// The general form of every defect in this file: a type spelling the switch did not
	// list wrote no bytes and returned nil, so the caller got a payload with a hole in
	// it and was told it had succeeded.
	source := "name: t\nendian: big\nfields:\n  - name: thing\n    type: base64\n"

	got, err := encodeHex(t, source, map[string]any{"thing": "AQID"})
	if err == nil {
		t.Fatalf("expected an error, got payload %q", got)
	}
	if !strings.Contains(err.Error(), "cannot encode type") {
		t.Errorf("error does not name the type: %v", err)
	}
}

func TestEncodeSkipsADerivedFieldInsideAFlaggedGroup(t *testing.T) {
	// A `type: number` field is computed from others and occupies no bytes. encodeFlagged
	// skipped one only if it also carried the deprecated `formula` spelling, so a field
	// using `ref` reached encodeField - where it wrote nothing, because the switch had no
	// case for `number`, and the group's bytes came out right by accident.
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: flags\n    type: u8\n" +
		"  - flagged:\n" +
		"      field: flags\n" +
		"      groups:\n" +
		"        - bit: 0\n" +
		"          fields:\n" +
		"            - name: raw\n              type: u8\n" +
		"            - name: scaled\n              type: number\n" +
		"              ref: $raw\n              mult: 2\n"

	got, err := encodeHex(t, source, map[string]any{"raw": 5.0, "scaled": 10.0})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	// The flags byte, then `raw` alone: `scaled` is derived and writes nothing.
	if got != "0105" {
		t.Errorf("encoded as %q, want %q", got, "0105")
	}
}

func TestEncodeWritesABitRangeField(t *testing.T) {
	// A bit range carries its range in the type string, so it never matches a plain type
	// name; encoding had no equivalent of decodeField's pre-switch check and wrote
	// nothing. One byte of the value, as the reference interpreter writes it.
	source := "name: t\nendian: big\nfields:\n" +
		"  - name: flag\n    type: u8[0:0]\n    lookup: [off, on]\n"

	got, err := encodeHex(t, source, map[string]any{"flag": "on"})
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if got != "01" {
		t.Errorf("encoded as %q, want %q", got, "01")
	}
}
