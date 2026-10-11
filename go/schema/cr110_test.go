package schema

import (
	"encoding/hex"
	"reflect"
	"strings"
	"testing"
)

// CR-2026-110 (PS-157, PS-525 to PS-527): on_repeat combines a name reported by more
// than one tlv entry. The decodes that succeed are the _language-conformance
// tlv-repeat-* fixtures; this holds the rejections, the twice-reported member and the
// encoder's output where it is lossy. Messages are the spec prototype's.

const cr110Loramac = `
name: t
fields:
  - tlv:
      tag_size: 2
      on_repeat: merge
      cases:
        0x0310: [{name: loramac, type: object, fields: [{name: adr, type: u8}]}]
        0x0311: [{name: loramac, type: object, fields: [{name: dr, type: u8}]}]
        0x0001: [{name: battery, type: u8}]
`

const cr110Event = `
name: t
fields:
  - tlv:
      on_repeat: last
      cases:
        0x3a: [{name: event, type: u8}]
        0x40: [{name: event, type: u8}]
`

func cr110Parse(t *testing.T, doc string) *Schema {
	t.Helper()
	s, err := ParseSchema(doc)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	return s
}

func cr110Hex(t *testing.T, h string) []byte {
	t.Helper()
	b, err := hex.DecodeString(h)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestCR110MergeCombinesObjectsAndSparesScalars(t *testing.T) {
	out, err := cr110Parse(t, cr110Loramac).Decode(cr110Hex(t, "031001031105000102000103"))
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	want := map[string]any{
		"loramac": map[string]any{"adr": float64(1), "dr": float64(5)},
		"battery": []any{float64(2), float64(3)},
	}
	if !reflect.DeepEqual(normalizeNumbers(out), want) {
		t.Errorf("decode = %#v, want %#v", out, want)
	}
}

func TestCR110MemberReportedTwiceIsAnError(t *testing.T) {
	_, err := cr110Parse(t, cr110Loramac).Decode(cr110Hex(t, "031001031002"))
	want := "tlv: the merged object 'loramac' reports its member 'adr' twice (PS-525)"
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Errorf("got %v, want %q", err, want)
	}
}

func TestCR110CollectedListValueIsOneElement(t *testing.T) {
	s := cr110Parse(t, `
name: t
fields:
  - tlv:
      cases:
        1: [{name: vals, type: repeat, count: 2, fields: [{name: v, type: u8}]}]
`)
	out, err := s.Decode(cr110Hex(t, "010102010304"))
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	vals, _ := out["vals"].([]any)
	if len(vals) != 2 {
		t.Fatalf("vals = %#v, want two arrays of two", out["vals"])
	}
	for _, v := range vals {
		if inner, ok := v.([]any); !ok || len(inner) != 2 {
			t.Errorf("element %#v is not an array of two", v)
		}
	}
}

func TestCR110MergeEncodesOneEntryPerCase(t *testing.T) {
	s := cr110Parse(t, cr110Loramac)
	data := map[string]any{"loramac": map[string]any{"dr": 5, "adr": 1}}
	for _, ordered := range []bool{false, true} {
		var got []byte
		var err error
		if ordered {
			_, order, derr := s.DecodeOrdered(cr110Hex(t, "031001031105"))
			if derr != nil {
				t.Fatal(derr)
			}
			got, err = s.EncodeOrdered(data, order)
		} else {
			got, err = s.Encode(data)
		}
		if err != nil || hex.EncodeToString(got) != "031001031105" {
			t.Errorf("ordered=%v: encode = %x, %v; want 031001031105", ordered, got, err)
		}
	}
}

func TestCR110LastEncodesOnceUnderFirstCase(t *testing.T) {
	s := cr110Parse(t, cr110Event)
	out, order, err := s.DecodeOrdered(cr110Hex(t, "3a014002"))
	if err != nil {
		t.Fatal(err)
	}
	if v, _ := toFloat64(out["event"]); v != 2 {
		t.Fatalf("decode = %#v, want event 2", out)
	}
	for _, got := range [][]byte{mustEncode(t, s, out, nil), mustEncode(t, s, out, order)} {
		if hex.EncodeToString(got) != "3a02" {
			t.Errorf("encode = %x, want 3a02", got)
		}
	}
}

func mustEncode(t *testing.T, s *Schema, data map[string]any, order []string) []byte {
	t.Helper()
	var got []byte
	var err error
	if order == nil {
		got, err = s.Encode(data)
	} else {
		got, err = s.EncodeOrdered(data, order)
	}
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	return got
}

func TestCR110RejectedAtLoad(t *testing.T) {
	cases := []struct{ tlv, want string }{
		{"cases:\n        1: [{name: e, type: u8, on_repeat: merge}]",
			"Field 'e': only a `type: object` field may declare `on_repeat: merge` (PS-526)"},
		{"cases:\n        1: [{name: e, type: u8, on_repeat: last}]\n        2: [{name: e, type: u8}]",
			"tlv: the fields reporting 'e' declare different `on_repeat`, last and collect (PS-526)"},
		{"on_repeat: first\n      cases:\n        1: [{name: e, type: u8}]",
			"tlv `on_repeat` is one of collect, merge, last, not 'first' (PS-525)"},
		{"cases:\n        1: [{name: e, type: u8, on_repeat: keep}]",
			"Field 'e': `on_repeat` is one of collect, merge, last, not 'keep' (PS-525)"},
	}
	for _, c := range cases {
		_, err := ParseSchema("name: t\nfields:\n  - tlv:\n      " + c.tlv + "\n")
		if err == nil || err.Error() != c.want {
			t.Errorf("got %v, want %q", err, c.want)
		}
	}
}

func TestCR110MergeTLVSparesScalar(t *testing.T) {
	cr110Parse(t, cr110Loramac) // battery is a u8 under a merge tlv: valid
}

// normalizeNumbers widens every integer to float64, so a DeepEqual is not about
// which integer type a path produced.
func normalizeNumbers(v any) any {
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, e := range x {
			out[k] = normalizeNumbers(e)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = normalizeNumbers(e)
		}
		return out
	}
	if f, ok := toFloat64(v); ok {
		return f
	}
	return v
}

// CR-2026-125 (PS-527, PS-525), the spec prototype's TestCR2026125OnRepeatEncoding.

const cr125Loramac = `
name: t
fields:
  - tlv:
      tag_size: 2
      on_repeat: merge
      cases:
        0x0310: [{name: loramac, type: object, fields: [{name: adr, type: u8}, {name: dr, type: u8}]}]
        0x0311: [{name: loramac, type: object, fields: [{name: dr, type: u8}]}]
`

// An array PS-157 collected is written under the first case declaring the name, one
// entry per element, on both encode paths.
func TestCR125CollectedNameWrittenUnderFirstCase(t *testing.T) {
	s := cr110Parse(t, strings.Replace(cr110Event, "on_repeat: last", "on_repeat: collect", 1))
	data := map[string]any{"event": []any{1, 2}}
	for _, order := range [][]string{nil, {"58", "64"}} {
		if got := hex.EncodeToString(mustEncode(t, s, data, order)); got != "3a013a02" {
			t.Errorf("order %v: encode = %s, want 3a013a02", order, got)
		}
	}
}

func TestCR125StrayMemberIsAnError(t *testing.T) {
	_, err := cr110Parse(t, cr125Loramac).Encode(map[string]any{
		"loramac": map[string]any{"adr": 1, "dr": 5, "fw": 3}})
	want := "tlv: the merged object 'loramac' is given ['fw'], which no case declares (PS-527)"
	if err == nil || err.Error() != want {
		t.Fatalf("encode error = %v, want %q", err, want)
	}
}

func TestCR125MemberTwoCasesDeclareWrittenOnce(t *testing.T) {
	data := map[string]any{"loramac": map[string]any{"adr": 1, "dr": 5}}
	if got := hex.EncodeToString(mustEncode(t, cr110Parse(t, cr125Loramac), data, nil)); got != "03100105" {
		t.Fatalf("encode = %s, want 03100105", got)
	}
}

func TestCR125OnRepeatWithoutEffectIsRejected(t *testing.T) {
	cases := []struct{ fields, name string }{
		{"  - {name: a, type: u8, on_repeat: last}", "a"},
		{"  - tlv:\n      cases:\n        1: [{name: o, type: object, fields: [{name: m, type: u8, on_repeat: last}]}]", "m"},
	}
	for _, c := range cases {
		_, err := ParseSchema("name: t\nfields:\n" + c.fields + "\n")
		want := "`on_repeat` on '" + c.name + "' has no effect there: it belongs on a tlv, a field of a tlv case, or a merged object's member (PS-525)"
		if err == nil || err.Error() != want {
			t.Errorf("got %v, want %q", err, want)
		}
	}
}

func TestCR125LastOnMergedMemberIsAccepted(t *testing.T) {
	s := cr110Parse(t, "name: t\nfields:\n  - tlv:\n      on_repeat: merge\n      cases:\n"+
		"        1: [{name: o, type: object, fields: [{name: m, type: u8, on_repeat: last}]}]\n")
	out, err := s.Decode(cr110Hex(t, "01050107"))
	if err != nil {
		t.Fatal(err)
	}
	if got := normalizeNumbers(out); !reflect.DeepEqual(got, map[string]any{"o": map[string]any{"m": 7.0}}) {
		t.Fatalf("decode = %#v", got)
	}
}
