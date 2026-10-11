package schema

import (
	"encoding/hex"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

// CR-2026-111: a repeat of single values (PS-528, PS-529, PS-531), an index counted from
// index_base (PS-530, PS-375), and null for an omitted element. The messages are the
// spec prototype's, as tools/schema_interpreter.py words them.

func cr111Parse(t *testing.T, fields string) *Schema {
	t.Helper()
	s, err := ParseSchema("name: t\nendian: big\nfields:\n" + fields)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	return s
}

func cr111Decode(t *testing.T, s *Schema, payload string) map[string]any {
	t.Helper()
	data, _ := hex.DecodeString(payload)
	out, err := s.Decode(data)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	return out
}

// asJSON normalises numbers so int and float64 compare equal.
func asJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestCR111LoadErrors(t *testing.T) {
	cases := []struct{ fields, want string }{
		{"  - name: v\n    type: repeat\n    count: 1\n    fields: [{name: a, type: u8}]\n    element: {type: u8}\n",
			"Repeat 'v' declares both of `fields` and `element`; it declares exactly one (PS-083, PS-528)"},
		{"  - name: v\n    type: repeat\n    count: 1\n",
			"Repeat 'v' declares neither of `fields` and `element`; it declares exactly one (PS-083, PS-528)"},
		{"  - name: v\n    type: repeat\n    count: 1\n    element: 3\n",
			"Repeat 'v': `element` is a single field definition (PS-528)"},
		{"  - name: v\n    type: repeat\n    count: 1\n    index: i\n    index_base: '1'\n    element: {type: u8}\n",
			"Repeat 'v': `index_base` is an integer (PS-530)"},
		{"  - name: v\n    type: repeat\n    count: 1\n    index: i\n    index_base: 1.5\n    element: {type: u8}\n",
			"Repeat 'v': `index_base` is an integer (PS-530)"},
		{"  - name: v\n    type: repeat\n    count: 1\n    index: i\n    index_base: true\n    element: {type: u8}\n",
			"Repeat 'v': `index_base` is an integer (PS-530)"},
		{"  - type: repeat\n    count: 1\n",
			"Repeat '<unnamed>' declares neither of `fields` and `element`; it declares exactly one (PS-083, PS-528)"},
	}
	for _, c := range cases {
		_, err := ParseSchema("name: t\nfields:\n" + c.fields)
		if err == nil || err.Error() != c.want {
			t.Errorf("got %v\nwant %s", err, c.want)
		}
	}
}

func TestCR111ElementIsCheckedLikeAnyField(t *testing.T) {
	_, err := ParseSchema("name: t\nfields:\n  - name: v\n    type: repeat\n    count: 1\n    element: {type: s17}\n")
	if err == nil {
		t.Fatal("an element with an unknown type must be rejected at load")
	}
}

func TestCR111Decode(t *testing.T) {
	cases := []struct{ fields, payload, want string }{
		{"  - {name: increments, type: repeat, until: end, element: {type: u16, div: 10}}\n",
			"00780096005A", `{"increments":[12,15,9]}`},
		{"  - {name: v, type: repeat, count: 3, element: {type: u8, sentinel: [255]}}\n",
			"01FF03", `{"v":[1,null,3]}`},
		{"  - {name: v, type: repeat, count: 2, element: {type: u8, lookup: {1: \"on\"}}}\n",
			"0102", `{"v":["on",null]}`},
		{"  - {name: v, type: repeat, count: 3, present_if: {field: $x, ne: 0}, element: {name: x, type: u8}}\n",
			"010002", `{"v":[1,2]}`},
		{"  - {name: devices, type: repeat, count: 2, index: i, index_base: 1, fields: [{name: id, type: u16, name_from: \"Device${i}ID\"}]}\n",
			"000A000B", `{"devices":[{"Device1ID":10},{"Device2ID":11}]}`},
		{"  - {name: p, type: repeat, until: end, element: {type: object, fields: [{name: k, type: u8}, {name: l, type: u8}]}}\n",
			"01020304", `{"p":[{"k":1,"l":2},{"k":3,"l":4}]}`},
	}
	for _, c := range cases {
		s := cr111Parse(t, c.fields)
		if got := asJSON(t, cr111Decode(t, s, c.payload)); got != c.want {
			t.Errorf("%s: got %s want %s", c.fields, got, c.want)
		}
		data, _ := hex.DecodeString(c.payload)
		out, _, err := s.DecodeOrdered(data)
		if err != nil || asJSON(t, out) != c.want {
			t.Errorf("DecodeOrdered %s: got %s, %v want %s", c.fields, asJSON(t, out), err, c.want)
		}
	}
}

func TestCR111Encode(t *testing.T) {
	s := cr111Parse(t, "  - {name: increments, type: repeat, until: end, element: {type: u16, div: 10}}\n")
	for _, encode := range []func(map[string]any) ([]byte, error){
		s.Encode,
		func(d map[string]any) ([]byte, error) { return s.EncodeOrdered(d, nil) },
	} {
		got, err := encode(map[string]any{"increments": []any{12, 15, 9}})
		if err != nil || hex.EncodeToString(got) != "00780096005a" {
			t.Errorf("encode = %x, %v", got, err)
		}
		_, err = encode(map[string]any{"increments": []any{12, nil}})
		want := "Repeat 'increments' has null at position 1, and its element declares no `sentinel` to write for it (PS-544)"
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("null position: got %v, want %s", err, want)
		}
	}
}

func TestCR111IndexBaseEncodesAsItDecodes(t *testing.T) {
	s := cr111Parse(t, "  - {name: v, type: repeat, count: 2, index: i, index_base: 1, fields: [{name: n, type: u8}, {name: pos, type: number, ref: $i}]}\n")
	if got := asJSON(t, cr111Decode(t, s, "0507")); got != `{"v":[{"n":5,"pos":1},{"n":7,"pos":2}]}` {
		t.Errorf("decode = %s", got)
	}
	out, err := s.Encode(map[string]any{"v": []any{map[string]any{"n": 5}, map[string]any{"n": 7}}})
	if err != nil || hex.EncodeToString(out) != "0507" {
		t.Errorf("encode = %x, %v", out, err)
	}
}

func TestCR111MetaNestsTheElement(t *testing.T) {
	s, err := ParseSchema(`name: t
fields:
  - name: temperatures
    type: repeat
    count: 8
    index: i
    index_base: 1
    identity: i
    element: {name: t, type: u16, unit: Cel}
`)
	if err != nil {
		t.Fatal(err)
	}
	out, err := s.Interpret(make([]byte, 16), InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	got := out["_meta"].(map[string]any)["fields"].(map[string]any)["temperatures"]
	want := map[string]any{
		"type":     "repeat",
		"element":  map[string]any{"type": "u16", "unit": "Cel"},
		"identity": map[string]any{"index": "i", "count": 8, "index_base": 1},
	}
	if asJSON(t, got) != asJSON(t, want) {
		t.Errorf("_meta = %s\nwant %s", asJSON(t, got), asJSON(t, want))
	}
}

func TestCR111ABaseOf0AddsNothing(t *testing.T) {
	s, err := ParseSchema(`name: t
fields:
  - {name: v, type: repeat, count: 2, index: i, identity: i, fields: [{name: a, type: u8}]}
`)
	if err != nil {
		t.Fatal(err)
	}
	out, err := s.Interpret([]byte{1, 2}, InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	got := out["_meta"].(map[string]any)["fields"].(map[string]any)["v"].(map[string]any)["identity"]
	if !reflect.DeepEqual(asJSON(t, got), `{"count":2,"index":"i"}`) {
		t.Errorf("identity = %s", asJSON(t, got))
	}
}
