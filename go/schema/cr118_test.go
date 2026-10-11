package schema

import (
	"strings"
	"testing"
)

// CR-2026-118: several records from one repeat element (PS-541, PS-542) and the set
// conditions `in` / `not_in` (PS-543). The messages are tools/schema_interpreter.py's.

const cr118Messages = `  - name: messages
    type: repeat
    until: end
    count_as: n
    index: i
    present_if: {field: $_dataId, not_in: [1, 2, 3, 4, 5, 6]}
    fields:
      - {name: _dataId, type: u8}
      - {name: pos, type: number, ref: $i}
      - match:
          field: $_dataId
          cases:
            7:
              - records:
                  - [{name: type, type: string, value: upload_battery}, {name: battery, type: u16}]
                  - [{name: type, type: string, value: upload_interval}, {name: interval, type: u16, mult: 60}]
            3: [{name: x, type: u8}]
            0: [{name: hw, type: u8}]
  - {name: count, type: number, ref: $n}
`

func TestCR118RecordsBecomeConsecutiveElements(t *testing.T) {
	s := cr111Parse(t, cr118Messages)
	got := asJSON(t, cr111Decode(t, s, "070064003C0305000A"))
	want := `{"count":3,"messages":[{"battery":100,"pos":0,"type":"upload_battery"},` +
		`{"interval":3600,"pos":0,"type":"upload_interval"},{"hw":10,"pos":2}]}`
	if got != want {
		t.Errorf("got  %s\nwant %s", got, want)
	}
}

func TestCR118ElementFieldsCopiedAndInternalsNot(t *testing.T) {
	s := cr111Parse(t, `  - name: m
    type: repeat
    count: 1
    fields:
      - {name: channel, type: u8}
      - {name: _spare, type: u8}
      - records: [[{name: a, type: u8}], [{name: b, type: u8}]]
`)
	got := asJSON(t, cr111Decode(t, s, "01FF0203"))
	if want := `{"m":[{"a":2,"channel":1},{"b":3,"channel":1}]}`; got != want {
		t.Errorf("got  %s\nwant %s", got, want)
	}
}

func TestCR118MetaElementsCoverEveryRecord(t *testing.T) {
	s := cr111Parse(t, cr118Messages)
	out, err := s.Interpret([]byte{7, 0, 0x64, 0, 0x3C}, InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	meta := out["_meta"].(map[string]any)["fields"].(map[string]any)["messages"].(map[string]any)
	elements := meta["elements"].(map[string]any)
	for _, name := range []string{"pos", "type", "battery", "interval", "x", "hw"} {
		if _, ok := elements[name]; !ok {
			t.Errorf("elements lacks %s: %v", name, elements)
		}
	}
}

func TestCR118LoadErrors(t *testing.T) {
	cases := []struct{ fields, want string }{
		{"  - records: [[{name: a, type: u8}]]\n",
			"`records` belongs in a repeat's element, or a match case within it (PS-541)"},
		{"  - {name: o, type: object, fields: [{records: [[{name: a, type: u8}]]}]}\n",
			"`records` belongs in a repeat's element, or a match case within it (PS-541)"},
		{"  - {name: m, type: repeat, count: 1, fields: [{records: []}]}\n",
			"`records` is a non-empty list of field lists (PS-541)"},
		{"  - {name: m, type: repeat, count: 1, fields: [{records: [{name: a, type: u8}]}]}\n",
			"`records` is a non-empty list of field lists (PS-541)"},
		{"  - {name: m, type: repeat, count: 1, element: {match: {length: 1, cases: {1: [{records: [[{name: a, type: u8}]]}]}}}}\n",
			"Repeat 'm': an `element` cannot reach a `records` entry (PS-541)"},
		{"  - {name: m, type: repeat, count: 1, present_if: {field: $a, in: [1, x]}, fields: [{name: a, type: u8}]}\n",
			"`in` in the present_if of 'm' is a list of numbers (PS-543)"},
		{"  - {name: m, type: repeat, count: 1, present_if: {field: $a, not_in: 3}, fields: [{name: a, type: u8}]}\n",
			"`not_in` in the present_if of 'm' is a list of numbers (PS-543)"},
		{"  - {name: a, type: u8}\n  - {name: b, type: number, ref: $a, guard: {when: [{field: $a, in: [1], gt: 0}]}}\n",
			"a guard condition of 'b' has 2 comparison operators; a condition has exactly one (PS-126)"},
		// CR-2026-124: PS-126 holds for every condition, and booleans are not numbers.
		{"  - {name: m, type: repeat, count: 1, present_if: {field: $a, gt: 0, lt: 3}, fields: [{name: a, type: u8}]}\n",
			"the present_if of 'm' has 2 comparison operators; a condition has exactly one (PS-126)"},
		{"  - {name: a, type: u8}\n  - {name: b, type: number, ref: $a, guard: {when: [{field: $a}], else: 0}}\n",
			"a guard condition of 'b' has 0 comparison operators; a condition has exactly one (PS-126)"},
		{"  - {name: a, type: u8}\n  - {name: b, type: number, ref: $a, guard: {when: [{field: $a, in: [true]}], else: 0}}\n",
			"`in` in a guard condition of 'b' is a list of numbers (PS-543)"},
		// CR-2026-124: a record field may not share a name the element reports outside.
		{"  - {name: m, type: repeat, count: 1, fields: [{name: channel, type: u8}, {match: {length: 1, cases: {1: [{name: kind, type: u8}]}}}, {records: [[{name: kind, type: u8}], [{name: channel, type: u8}]]}]}\n",
			"Repeat 'm': ['channel', 'kind'] is reported both by a record and by the element outside `records` (PS-541)"},
		{"  - {name: m, type: repeat, count: 1, fields: [{name: _c, type: u8}, {name: o, type: object, fields: [{name: b, type: u8}]}, {records: [[{name: _c, type: u8}], [{name: b, type: u8}]]}]}\n",
			""},
	}
	for _, c := range cases {
		_, err := ParseSchema("name: t\nfields:\n" + c.fields)
		if c.want == "" {
			if err != nil {
				t.Errorf("%s: an internal or object-member name is no clash, got %v", c.fields, err)
			}
			continue
		}
		if err == nil || err.Error() != c.want {
			t.Errorf("%s: got %v\nwant %s", c.fields, err, c.want)
		}
	}
}

func TestCR118EncodeIsRefused(t *testing.T) {
	s := cr111Parse(t, `  - name: m
    type: repeat
    count: 1
    fields:
      - records: [[{name: a, type: u8}]]
`)
	want := "Repeat 'm' has elements that yield several records, which cannot be encoded (PS-542)"
	if _, err := s.Encode(map[string]any{"m": []any{map[string]any{"a": 1}}}); err == nil || !strings.Contains(err.Error(), want) {
		t.Errorf("Encode: got %v", err)
	}
	if _, err := s.EncodeOrdered(map[string]any{"m": []any{map[string]any{"a": 1}}}, nil); err == nil || !strings.Contains(err.Error(), want) {
		t.Errorf("EncodeOrdered: got %v", err)
	}
}

func TestCR118InAndNotIn(t *testing.T) {
	for _, c := range []struct{ op, want string }{
		{"in", `{"v":[{"x":1},{"x":3}]}`},
		{"not_in", `{"v":[{"x":2}]}`},
		// CR-2026-124 (PS-543): an empty `in` never holds, an empty `not_in` always.
		{"in: [], x", `{"v":[]}`},
		{"not_in: [], x", `{"v":[{"x":1},{"x":2},{"x":3}]}`},
	} {
		list := ": [1, 3]"
		op := c.op
		if strings.HasSuffix(op, ", x") {
			op, list = strings.TrimSuffix(op, ", x"), ""
		}
		s := cr111Parse(t, "  - {name: v, type: repeat, count: 3, present_if: {field: $x, "+op+list+"}, fields: [{name: x, type: u8}]}\n")
		if got := asJSON(t, cr111Decode(t, s, "010203")); got != c.want {
			t.Errorf("%s: got %s want %s", c.op, got, c.want)
		}
	}
	s := cr111Parse(t, "  - {name: mode, type: u8}\n  - {name: setting, type: number, ref: $mode, mult: 10, guard: {when: [{field: $mode, in: [2, 4]}], else: -1}}\n")
	if got := asJSON(t, cr111Decode(t, s, "04")); got != `{"mode":4,"setting":40}` {
		t.Errorf("guard in holds: %s", got)
	}
	if got := asJSON(t, cr111Decode(t, s, "05")); got != `{"mode":5,"setting":-1}` {
		t.Errorf("guard in fails: %s", got)
	}
}
