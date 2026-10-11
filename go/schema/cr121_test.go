package schema

import (
	"encoding/hex"
	"strings"
	"testing"
)

// CR-2026-121 (PS-544, PS-545), CR-2026-123 (PS-546, PS-547) and CR-2026-126 (PS-548),
// mirroring tests/test_cr_2026_121_123_126.py and the spec prototype's
// TestCR2026121ElementNullsAndShapes, TestCR2026123IdentityFromASibling and
// TestCR2026126IpsoInstances.

func cr121LoadError(t *testing.T, fields string) error {
	t.Helper()
	_, err := ParseSchema("name: t\nendian: big\nfields:\n" + fields)
	return err
}

func cr121Encode(t *testing.T, s *Schema, data map[string]any) (string, error) {
	t.Helper()
	out, err := s.Encode(data)
	return hex.EncodeToString(out), err
}

const cr121Sentinel = "  - {name: v, type: repeat, count: 3, element: {type: u16, sentinel: [0xFFFF, 0xFFFE]}}\n"

func TestCR121NullRoundTripsThroughTheFirstSentinel(t *testing.T) {
	s := cr111Parse(t, cr121Sentinel)
	decoded := cr111Decode(t, s, "0001FFFF0003")
	if got := asJSON(t, decoded); got != `{"v":[1,null,3]}` {
		t.Fatalf("decode = %s", got)
	}
	if got, err := cr121Encode(t, s, decoded); err != nil || got != "0001ffff0003" {
		t.Errorf("encode = %s, %v", got, err)
	}
	// The second sentinel decodes to null too, and re-encodes as the first.
	if got, err := cr121Encode(t, s, cr111Decode(t, s, "0001FFFE0003")); err != nil || got != "0001ffff0003" {
		t.Errorf("second sentinel re-encodes = %s, %v", got, err)
	}
}

func TestCR121SentinelIsRawBits(t *testing.T) {
	s := cr111Parse(t, "  - {name: v, type: repeat, count: 2, element: {type: u16, div: 10, sentinel: [0xFFFF]}}\n")
	if got, err := cr121Encode(t, s, map[string]any{"v": []any{0.5, nil}}); err != nil || got != "0005ffff" {
		t.Errorf("encode = %s, %v", got, err)
	}
}

func TestCR121NullWithoutSentinelIsRefused(t *testing.T) {
	s := cr111Parse(t, "  - {name: v, type: repeat, count: 2, element: {type: u8}}\n")
	_, err := s.Encode(map[string]any{"v": []any{1, nil}})
	want := "Repeat 'v' has null at position 1, and its element declares no `sentinel` to write for it (PS-544)"
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Errorf("got %v, want %s", err, want)
	}
}

func TestCR121InternalElementNameIsReported(t *testing.T) {
	s := cr111Parse(t, "  - {name: v, type: repeat, count: 2, element: {name: _x, type: u8}}\n")
	if got := asJSON(t, cr111Decode(t, s, "0102")); got != `{"v":[1,2]}` {
		t.Errorf("decode = %s", got)
	}
	if got, err := cr121Encode(t, s, map[string]any{"v": []any{1, 2}}); err != nil || got != "0102" {
		t.Errorf("encode = %s, %v", got, err)
	}
	s = cr111Parse(t, "  - {name: v, type: repeat, count: 3, present_if: {field: $_x, ne: 0}, element: {name: _x, type: u8}}\n")
	if got := asJSON(t, cr111Decode(t, s, "010002")); got != `{"v":[1,2]}` {
		t.Errorf("present_if on $_x: decode = %s", got)
	}
}

func TestCR121ConstructElementIsRejected(t *testing.T) {
	want := "Repeat 'v': `element` is a data field that declares `type`, not a construct (PS-545)"
	for _, element := range []string{
		"{byte_group: [{name: a, type: 'u8[0:3]'}]}",
		"{name: m, match: {length: 1, cases: {}}}",
		"{name: f, type: u8, flagged: {field: $x, groups: []}}",
		"{name: n}",
	} {
		err := cr121LoadError(t, "  - {name: v, type: repeat, count: 3, element: "+element+"}\n")
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%s: got %v", element, err)
		}
	}
}

func TestCR121ObjectElementReportsOneObject(t *testing.T) {
	s := cr111Parse(t, "  - {name: v, type: repeat, count: 2, element: {name: o, type: object, fields: [{name: a, type: u8}, {name: b, type: u8}]}}\n")
	if got := asJSON(t, cr111Decode(t, s, "01020304")); got != `{"v":[{"a":1,"b":2},{"a":3,"b":4}]}` {
		t.Errorf("decode = %s", got)
	}
}

const cr123Codes = "  - {name: codes, type: repeat, count: 2, element: {type: u8, lookup: {1: '1.8.1', 2: '2.8.1', default: '${value}'}}}\n"

func cr123Values(count, extra string) string {
	return "  - {name: values, type: repeat, count: " + count + ", index: i, identity: '$codes[i]', element: {type: u32}" + extra + "}\n"
}

func TestCR123TwoArraysAndTheMetaIdentity(t *testing.T) {
	s := cr111Parse(t, cr123Codes+cr123Values("2", ""))
	data, _ := hex.DecodeString("01020000007B000001C8")
	out, err := s.Interpret(data, InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	meta := out["_meta"].(map[string]any)["fields"].(map[string]any)["values"]
	delete(out, "_meta")
	if got := asJSON(t, out); got != `{"codes":["1.8.1","2.8.1"],"values":[123,456]}` {
		t.Errorf("decode = %s", got)
	}
	want := `{"element":{"type":"u32"},"identity":{"open":true,"repeat":"codes","values":["1.8.1","2.8.1","${value}"]},"type":"repeat"}`
	if got := asJSON(t, meta); got != want {
		t.Errorf("_meta = %s\nwant %s", got, want)
	}
}

func TestCR123ClosedListingAndSiblingUnit(t *testing.T) {
	s := cr111Parse(t, "  - {name: codes, type: repeat, count: 2, element: {type: u8, lookup: {1: a, 2: b}}}\n"+cr123Values("2", ""))
	data, _ := hex.DecodeString("01020000007B000001C8")
	out, err := s.Interpret(data, InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	got := out["_meta"].(map[string]any)["fields"].(map[string]any)["values"].(map[string]any)["identity"]
	if asJSON(t, got) != `{"repeat":"codes","values":["a","b"]}` {
		t.Errorf("identity = %s", asJSON(t, got))
	}
	s = cr111Parse(t, "  - {name: units, type: repeat, count: 2, element: {type: u8, lookup: {0: Wh, 1: W}}}\n"+
		"  - {name: values, type: repeat, count: 2, index: i, element: {type: u32, unit: '$units[i]'}}\n")
	data, _ = hex.DecodeString("00010000007B000001C8")
	out, err = s.Interpret(data, InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	got = out["_meta"].(map[string]any)["fields"].(map[string]any)["values"].(map[string]any)["element"].(map[string]any)["unit"]
	if asJSON(t, got) != `{"repeat":"units","values":["Wh","W"]}` {
		t.Errorf("unit = %s", asJSON(t, got))
	}
}

func TestCR123DifferentLengthsAreAnError(t *testing.T) {
	s := cr111Parse(t, cr123Codes+cr123Values("1", ""))
	data, _ := hex.DecodeString("01020000007B")
	_, err := s.Decode(data)
	want := "Repeat 'values' has 1 values and 'codes', which identifies them, has 2 (PS-546)"
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Errorf("got %v, want %s", err, want)
	}
}

func TestCR123NotAnEarlierLabelledSiblingIsRejected(t *testing.T) {
	for label, fields := range map[string]string{
		"not earlier":    cr123Values("2", "") + cr123Codes,
		"wrong index":    cr123Codes + "  - {name: values, type: repeat, count: 2, index: j, identity: '$codes[i]', element: {type: u32}}\n",
		"no lookup":      "  - {name: codes, type: repeat, count: 2, element: {type: u8}}\n" + cr123Values("2", ""),
		"not an element": "  - {name: codes, type: repeat, count: 2, fields: [{name: c, type: u8}]}\n" + cr123Values("2", ""),
	} {
		err := cr121LoadError(t, fields)
		if err == nil || !strings.Contains(err.Error(), "PS-546") {
			t.Errorf("%s: got %v", label, err)
		}
	}
}

func TestCR123CountFromAComputedField(t *testing.T) {
	for _, kind := range []string{"integer", "number"} {
		s := cr111Parse(t, "  - {name: total, type: u8}\n  - {name: _n, type: "+kind+", compute: {op: sub, a: $total, b: 2}}\n"+
			"  - {name: v, type: repeat, count: $_n, element: {type: u8}}\n")
		if got := asJSON(t, cr111Decode(t, s, "040A0B")); got != `{"total":4,"v":[10,11]}` {
			t.Errorf("%s: decode = %s", kind, got)
		}
		if got, err := cr121Encode(t, s, map[string]any{"total": 4, "v": []any{10, 11}}); err != nil || got != "040a0b" {
			t.Errorf("%s: encode = %s, %v", kind, got, err)
		}
	}
}

const cr126Message = "its ipso.instance can be other than an integer from 0 to 65534 (PS-548)"

func cr126Soil(lookup string) string {
	return "  - {name: s, type: repeat, count: 2, index: i, fields: [{name: k, type: u8, lookup: " + lookup +
		"}, {name: t, type: s16, ipso: {object: 3303, instance: $k}}]}\n"
}

func TestCR126IpsoInstance(t *testing.T) {
	accepted := map[string]string{
		"integer labels": cr126Soil("{0: 1, 1: 2}"),
		"literal 0":      "  - {name: t, type: u8, ipso: {object: 3303, instance: 0}}\n",
		"literal 65534":  "  - {name: t, type: u8, ipso: {object: 3303, instance: 65534}}\n",
		"index fits":     "  - {name: s, type: repeat, count: 8, index: i, index_base: 65527, element: {type: u8, ipso: {object: 3303, instance: $i}}}\n",
		"sibling ints": "  - {name: units, type: repeat, count: 2, element: {type: u8, lookup: {0: 3, 1: 4}}}\n" +
			"  - {name: v, type: repeat, count: 2, index: i, element: {type: u32, ipso: {object: 3305, instance: '$units[i]'}}}\n",
	}
	for label, fields := range accepted {
		if err := cr121LoadError(t, fields); err != nil {
			t.Errorf("%s: rejected: %v", label, err)
		}
	}
	rejected := map[string]string{
		"string labels":   cr126Soil("{0: soil, 1: air}"),
		"too large":       cr126Soil("{0: 70000}"),
		"open default":    cr126Soil("{0: '1', default: '${value}'}"),
		"literal string":  "  - {name: t, type: u8, ipso: {object: 3303, instance: two}}\n",
		"literal -1":      "  - {name: t, type: u8, ipso: {object: 3303, instance: -1}}\n",
		"literal 65535":   "  - {name: t, type: u8, ipso: {object: 3303, instance: 65535}}\n",
		"literal true":    "  - {name: t, type: u8, ipso: {object: 3303, instance: true}}\n",
		"literal 1.5":     "  - {name: t, type: u8, ipso: {object: 3303, instance: 1.5}}\n",
		"index past":      "  - {name: s, type: repeat, count: 8, index: i, index_base: 65528, element: {type: u8, ipso: {object: 3303, instance: $i}}}\n",
		"sibling strings": "  - {name: units, type: repeat, count: 2, element: {type: u8, lookup: {0: Wh, 1: W}}}\n" +
			"  - {name: v, type: repeat, count: 2, index: i, element: {type: u32, ipso: {object: 3305, instance: '$units[i]'}}}\n",
	}
	for label, fields := range rejected {
		err := cr121LoadError(t, fields)
		if err == nil || !strings.Contains(err.Error(), cr126Message) {
			t.Errorf("%s: got %v", label, err)
		}
	}
	err := cr121LoadError(t, "  - {name: t, type: u8, ipso: {object: 3303, instance: two}}\n")
	if err == nil || !strings.Contains(err.Error(), "Field 't': "+cr126Message) {
		t.Errorf("literal message: %v", err)
	}
}
