package schema

// _meta in the interpreter output: CR-2026-096 (PS-490 to PS-497), with PS-175 to
// PS-181, PS-340 to PS-342, PS-371 to PS-376, CR-2026-088's PS-480/481 and
// CR-2026-095's PS-489. Mirrors tests/test_cr_2026_096_meta.py. The fixtures
// `_language-conformance/meta-*.yaml` hold the output itself through the corpus runner;
// what a fixture cannot state is here.

import (
	"reflect"
	"strings"
	"testing"
)

const metaFlat = `
name: flat
fields:
  - {name: a, type: u8, unit: V}
  - {name: b, type: u8}
`

func mustParse(t *testing.T, text string) *Schema {
	t.Helper()
	s, err := ParseSchema(text)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	return s
}

func TestMetaDecodeIsUnchanged(t *testing.T) {
	s := mustParse(t, metaFlat)
	decoded, err := s.Decode([]byte{1, 2})
	if err != nil {
		t.Fatal(err)
	}
	if _, present := decoded["_meta"]; present || len(decoded) != 2 {
		t.Fatalf("Decode changed: %v", decoded)
	}
	out, err := s.Interpret([]byte{1, 2}, InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	if _, present := out["_meta"]; !present {
		t.Fatalf("no _meta: %v", out)
	}
}

func TestMetaPresentWithoutAnnotations(t *testing.T) {
	// PS-180. A Go map has no order, so "last" is not expressible; the key is there.
	out, err := mustParse(t, metaFlat).Interpret([]byte{1, 2}, InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"schema": "flat",
		"fields": map[string]any{
			"a": map[string]any{"type": "u8", "unit": "V"},
			"b": map[string]any{"type": "u8"},
		},
	}
	if ok, detail := corpusMetaMatches(want, out["_meta"], "_meta"); !ok {
		t.Fatal(detail)
	}
}

func TestMetaFailedDecodeHasNoMeta(t *testing.T) {
	// PS-497.
	out, err := mustParse(t, metaFlat).Interpret([]byte{1}, InputContext{})
	if err == nil || out != nil {
		t.Fatalf("expected a failed decode, got %v", out)
	}
}

func TestMetaFPortFromTheContext(t *testing.T) {
	s := mustParse(t, metaFlat)
	five := 5
	out, err := s.Interpret([]byte{1, 2}, InputContext{FPort: &five})
	if err != nil {
		t.Fatal(err)
	}
	if got := out["_meta"].(map[string]any)["fPort"]; got != 5 {
		t.Fatalf("fPort = %v", got)
	}
	out, _ = s.Interpret([]byte{1, 2}, InputContext{})
	if _, present := out["_meta"].(map[string]any)["fPort"]; present {
		t.Fatal("fPort reported though none was supplied (PS-341)")
	}

	// The runner's context: the vector's fPort, else input_metadata's.
	in := corpusInputContext(corpusVector{InputMetadata: map[string]any{"fPort": 6}})
	if in.FPort == nil || *in.FPort != 6 {
		t.Fatalf("input_metadata fPort not used: %v", in.FPort)
	}
	seven := 7
	in = corpusInputContext(corpusVector{FPort: &seven, InputMetadata: map[string]any{"fPort": 6}})
	if *in.FPort != 7 {
		t.Fatalf("the vector's fPort should win: %v", *in.FPort)
	}
}

func TestMetaDevEUIIsNormalised(t *testing.T) {
	cases := []struct {
		text, eui string
		ok        bool
	}{
		{"0011223344556677", "0011223344556677", true},
		{"00-11-22-33-44-55-66-AA", "00112233445566aa", true},
		{"00:11:22:33:44:55:66:AA", "00112233445566aa", true},
		{"00 11 22 33 44 55 66 aa", "00112233445566aa", true},
		{"0011", "", false},
		{"00112233445566GG", "", false},
	}
	for _, c := range cases {
		eui, ok := normaliseDevEUI(c.text)
		if eui != c.eui || ok != c.ok {
			t.Errorf("%q: got %q %v", c.text, eui, ok)
		}
	}
}

func TestMetaMalformedContextFails(t *testing.T) {
	s := mustParse(t, metaFlat)
	cases := []struct {
		in      InputContext
		message string
	}{
		{InputContext{DevEUI: "nonsense"}, "devEUI 'nonsense' is not 16 hexadecimal digits (PS-496)"},
		{InputContext{RecvTime: "yesterday"},
			"recvTime 'yesterday' is not an ISO 8601 time or a number of seconds (PS-495)"},
		{InputContext{RecvTime: true},
			"recvTime True is not an ISO 8601 time or a number of seconds (PS-495)"},
	}
	for _, c := range cases {
		out, err := s.Interpret([]byte{1, 2}, c.in)
		if out != nil || err == nil || err.Error() != c.message {
			t.Errorf("got %v, %v; want error %q", out, err, c.message)
		}
	}
}

func TestMetaRecvTimeIsUnixSeconds(t *testing.T) {
	cases := []struct {
		recv any
		want any
	}{
		{"2026-08-26T12:00:00Z", int64(1787745600)},
		{"2026-08-26T12:00:00.123Z", 1787745600.123},
		{"2026-08-26T12:00:00.120000Z", 1787745600.12},
		{"2026-08-26T12:00:00.1234Z", 1787745600.123},
		{"2026-08-26T12:00:00.1235Z", 1787745600.124},
		{"2026-08-26T12:00:00.1225Z", 1787745600.122},
		{"2026-08-26T12:00:00.9996Z", int64(1787745601)},
		{"2026-08-26T12:00:00.000Z", int64(1787745600)},
		{"2026-08-26T14:00:00+02:00", int64(1787745600)},
		{"2026-08-26T07:30:00-0430", int64(1787745600)},
		{"2026-08-26T12:00:00", int64(1787745600)},
		{"2026-08-26 12:00:00z", int64(1787745600)},
		{1787745600.5, 1787745600.5},
		{1787745600, 1787745600},
	}
	for _, c := range cases {
		got, ok := rxTimeSeconds(c.recv)
		// The type matters: a whole number of seconds is an integer, and a fraction is
		// the double the decimal literal reads as, bit for bit.
		if !ok || reflect.TypeOf(got) != reflect.TypeOf(c.want) || got != c.want {
			t.Errorf("%v: got %v (%T), want %v (%T)", c.recv, got, got, c.want, c.want)
		}
	}
	for _, bad := range []any{"yesterday", true, "2026-13-01T00:00:00Z", []any{}} {
		if got, ok := rxTimeSeconds(bad); ok {
			t.Errorf("%v: accepted as %v", bad, got)
		}
	}
}

func metaAgreementSchema(second string) string {
	return `
name: x
fields:
  - {name: _k, type: u8}
  - match:
      field: $_k
      cases:
        1: [{name: r, type: u8, unit: Cel}]
        2: [{name: r, ` + second + `}]
`
}

func TestMetaDeclarationsThatDisagreeAreRejected(t *testing.T) {
	// PS-491: unit, senml and ipso must agree; type and description may differ.
	for _, second := range []string{
		"type: s16, unit: Cel",
		"type: u8, unit: Cel, description: other",
	} {
		if _, err := ParseSchema(metaAgreementSchema(second)); err != nil {
			t.Errorf("%s: rejected: %v", second, err)
		}
	}
	for second, differing := range map[string]string{
		"type: u8, unit: K":                           "unit",
		"type: u8, unit: Cel, senml: {name: t}":       "senml",
		"type: u8, unit: Cel, ipso: {object: 3303}":   "ipso",
		"type: u8, unit: K, ipso: {object: 3303}":     "unit, ipso",
		"type: u8, unit: Cel, ipso: {object: 3303.0}": "ipso",
	} {
		_, err := ParseSchema(metaAgreementSchema(second))
		want := "Field 'r' is declared 2 times in fields and its declarations differ in " +
			differing + "; the declarations of one reported name must agree on unit, senml " +
			"and ipso (PS-491)"
		if err == nil || err.Error() != want {
			t.Errorf("%s: got %v, want %q", second, err, want)
		}
	}
}

func TestMetaAgreementIsPerFieldList(t *testing.T) {
	// PS-491's scope is one field list: different ports need not agree.
	_, err := ParseSchema(`
name: x
ports:
  1: {fields: [{name: r, type: u8, unit: Cel}]}
  2: {fields: [{name: r, type: u8, unit: K}]}
`)
	if err != nil {
		t.Fatalf("rejected: %v", err)
	}
	// But one port's list is checked, and named by its key; an object's members are a
	// list of their own, named by its path.
	_, err = ParseSchema(`
name: x
ports:
  3:
    fields:
      - {name: r, type: u8, unit: Cel}
      - {name: r, type: u8, unit: K}
`)
	if err == nil || !strings.Contains(err.Error(), "declared 2 times in port 3 ") {
		t.Fatalf("got %v", err)
	}
	_, err = ParseSchema(`
name: x
fields:
  - {name: r, type: u8, unit: K}
  - name: o
    type: object
    fields:
      - {name: r, type: u8, unit: Cel}
      - {name: r, type: u8, unit: V}
`)
	if err == nil || !strings.Contains(err.Error(), "declared 2 times in fields/o ") {
		t.Fatalf("got %v", err)
	}
}

func TestMetaProducerAndFallback(t *testing.T) {
	// PS-490: the declaration that wrote the value; a match's `name` falls back to its
	// pseudo-declaration; a name_from key is its declaration's (PS-492).
	s := mustParse(t, `
name: x
fields:
  - match:
      length: 1
      name: kind
      cases:
        1: [{name: r, type: u8, unit: Cel, description: one}]
        2: [{name: r, type: s16, unit: Cel, description: two}]
  - {name: idx, type: u8, var: idx}
  - {name: reading, name_from: "ch_${idx}", type: uint8}
`)
	out, err := s.Interpret([]byte{2, 0xff, 0x9c, 3, 9}, InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"schema": "x",
		"fields": map[string]any{
			"kind": map[string]any{"type": "u8"},
			"r":    map[string]any{"type": "s16", "unit": "Cel", "description": "two"},
			"idx":  map[string]any{"type": "u8"},
			"ch_3": map[string]any{"type": "u8"},
		},
	}
	if ok, detail := corpusMetaMatches(want, out["_meta"], "_meta"); !ok {
		t.Fatal(detail)
	}
}

func TestMetaLookupLabelsKeepSchemaOrder(t *testing.T) {
	// PS-375: a mapping lookup's labels in schema order, default last - here an order
	// no sort would produce.
	s := mustParse(t, `
name: x
fields:
  - name: list
    type: repeat
    count: 1
    identity: $k
    fields:
      - {name: k, type: u8, lookup: {9: nine, 2: two, default: other, 5: five}}
`)
	out, err := s.Interpret([]byte{2}, InputContext{})
	if err != nil {
		t.Fatal(err)
	}
	identity := out["_meta"].(map[string]any)["fields"].(map[string]any)["list"].(map[string]any)["identity"]
	want := map[string]any{"field": "k", "values": []any{"nine", "two", "five", "other"}}
	if ok, detail := corpusMetaMatches(want, identity, "identity"); !ok {
		t.Fatal(detail)
	}
}

func TestMetaMatchesIsExact(t *testing.T) {
	check := func(want, got any, expect bool) {
		t.Helper()
		if ok, _ := corpusMetaMatches(want, got, "_meta"); ok != expect {
			t.Errorf("%v vs %v: got %v", want, got, ok)
		}
	}
	check(map[string]any{"a": 1}, map[string]any{"a": 1.0}, true)
	check(map[string]any{"a": 1}, map[string]any{"a": 1, "b": 2}, false)
	check(map[string]any{"a": 1, "b": 2}, map[string]any{"a": 1}, false)
	check(map[string]any{"a": true}, map[string]any{"a": 1}, false)
	check(map[string]any{"a": []any{1, 2}}, map[string]any{"a": []any{2, 1}}, false)
	_, detail := corpusMetaMatches(
		map[string]any{"f": map[string]any{"x": map[string]any{"type": "u8"}}},
		map[string]any{"f": map[string]any{"x": map[string]any{"type": "s8"}}}, "_meta")
	if !strings.Contains(detail, "_meta.f.x.type") {
		t.Errorf("detail %q", detail)
	}
}

// A tlv with merge: false is refused by the encoder rather than written as nothing: its
// entries are a list under `channels`, and encoding them is not defined yet.
func TestEncodeRefusesASeparateTLV(t *testing.T) {
	s := mustParse(t, "name: x\nfields:\n  - tlv:\n      tag_size: 1\n      length_size: 1\n      merge: false\n      cases:\n        1:\n          - {name: a, type: u8}\n")
	data := map[string]any{"channels": []any{map[string]any{"tag": []any{1}, "a": 7}}}
	if _, err := s.EncodeToResult(data, 0); err == nil || !strings.Contains(err.Error(), "merge: false") {
		t.Fatalf("expected a refusal naming merge: false, got %v", err)
	}
}
