// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"bytes"
	"testing"
)

// CR-2026-060: a lookup default may carry the value it could not map. The shared fixture
// lookup-default-template.yaml holds the decode and round trip; these hold what a vector
// cannot - the rejections, and a value that is not an integer.
const cr060Schema = `name: p
fields:
  - name: sensor
    type: u8
    lookup: {0: Battery Voltage, 1: AIN1, default: "AnalogSensor${value}"}
  - name: half
    type: u8
    div: 2
    lookup: {1: one, default: "v${value}/${value}"}
`

func TestCR060DecodeAndRecover(t *testing.T) {
	s, err := ParseSchema(cr060Schema)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct {
		payload       []byte
		sensor, half string
	}{
		{[]byte{0x01, 0x02}, "AIN1", "one"},
		{[]byte{0x07, 0x05}, "AnalogSensor7", "v2.5/2.5"}, // 2.5 is no key, not key 2
		{[]byte{0xFF, 0x02}, "AnalogSensor255", "one"},
	} {
		out, err := s.Decode(c.payload)
		if err != nil || out["sensor"] != c.sensor || out["half"] != c.half {
			t.Errorf("% x: %v %v, want %s %s", c.payload, out, err, c.sensor, c.half)
			continue
		}
		back, err := s.Encode(out)
		if err != nil || !bytes.Equal(back, c.payload) {
			t.Errorf("% x: re-encoded % x %v (PS-409)", c.payload, back, err)
		}
	}
}

func TestCR060Rejections(t *testing.T) {
	s, err := ParseSchema(cr060Schema)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Encode(map[string]any{"sensor": "Sensor7", "half": "one"}); err == nil {
		t.Error("a string that is neither a label nor a template match was encoded (PS-409)")
	}
	if _, err := s.Encode(map[string]any{"sensor": "AIN1", "half": "v2.5/3"}); err == nil {
		t.Error("two ${value} holding different numbers matched the template (PS-409)")
	}
	if _, err := ParseSchema("name: p\nfields:\n  - {name: x, type: u8, lookup: {1: 10, default: \"x${value}\"}}\n"); err == nil {
		t.Error("numeric labels beside a ${value} default were accepted (PS-407)")
	}
}

func TestCR060ValueFormatting(t *testing.T) {
	// Matches JavaScript's String(number), which the reference interpreter also writes.
	for v, want := range map[float64]string{
		7: "7", -3: "-3", 2.5: "2.5", 1e-5: "0.00001", 1e-6: "0.000001", 1.5e-7: "1.5e-7",
		1e-8: "1e-8", 1e20: "100000000000000000000", 1e21: "1e+21", 1.5e22: "1.5e+22",
	} {
		if got := formatLookupValue(v); got != want {
			t.Errorf("%v: %s, want %s", v, got, want)
		}
	}
}

func TestCR060PS408LabelIsLiteral(t *testing.T) {
	s, err := ParseSchema("name: p\nfields:\n  - {name: x, type: u8, lookup: {1: \"a${value}\"}}\n")
	if err != nil {
		t.Fatal(err)
	}
	if out, _ := s.Decode([]byte{1}); out["x"] != "a${value}" {
		t.Errorf("a label is not a template (PS-408): %v", out["x"])
	}
}
