// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"bytes"
	"reflect"
	"strings"
	"testing"
)

// 0.5.2 wave 6a: the repeat iterator and reserved trailers. The _language-conformance
// fixtures hold the decodes; these hold what a vector cannot express - rejections,
// errors and encoding. Mirrors tests/test_wave6a_repeat_iterator.py.

// wave6aRepeat is the Python test's repeat(): a one-byte element read to the end, with
// `extra` spliced into the repeat's mapping.
func wave6aRepeat(extra string) string {
	if extra != "" {
		extra = ", " + extra
	}
	return `{"name": "probe", "fields": [{"name": "r", "type": "repeat", "until": "end", ` +
		`"fields": [{"name": "v", "type": "u8"}]` + extra + `}]}`
}

func wave6aRepeatFields(extra, fields string) string {
	if extra != "" {
		extra = ", " + extra
	}
	return `{"name": "probe", "fields": [{"name": "r", "type": "repeat", "until": "end", ` +
		`"fields": ` + fields + extra + `}]}`
}

func TestWave6aRejections(t *testing.T) {
	cases := []struct{ tag, schema string }{
		{"PS-350", `{"name": "p", "fields": [{"name": "r", "type": "repeat", "count": 1, "reserve": 1, "fields": [{"name": "v", "type": "u8"}]}]}`},
		{"PS-350", wave6aRepeat(`"reserve": -1`)},
		{"PS-383", wave6aRepeat(`"trailer": [{"name": "t", "type": "u8"}]`)},
		{"PS-384", wave6aRepeat(`"reserve": 2, "trailer": [{"name": "t", "type": "u8"}]`)},
		{"PS-384", wave6aRepeat(`"reserve": 1, "trailer": [{"name": "t", "type": "ascii", "length": "remaining"}]`)},
		{"PS-369", wave6aRepeat(`"index": "v"`)},
		{"PS-369", wave6aRepeat(`"index": "k", "count_as": "k"`)},
		{"PS-369", `{"name": "p", "fields": [{"name": "outer", "type": "repeat", "count": 1, "index": "i", "fields": [{"name": "inner", "type": "repeat", "count": 1, "index": "i", "fields": [{"name": "v", "type": "u8"}]}]}]}`},
		{"PS-386", wave6aRepeat(`"present_if": {"field": "$v"}`)},
		{"PS-372", wave6aRepeat(`"identity": "$nope"`)},
		{"PS-381", wave6aRepeatFields("", `[{"name": "v", "type": "u8", "carry": 0}]`)},
		{"PS-381", `{"name": "p", "fields": [{"name": "x", "type": "number", "value": 1, "carry": 0}]}`},
		{"PS-381", wave6aRepeatFields("", `[{"name": "v", "type": "u8"}, {"name": "t", "type": "number", "carry": "zero", "compute": {"op": "add", "a": "$t", "b": "$v"}}]`)},
		{"PS-374", wave6aRepeatFields("", `[{"name": "code", "type": "u8"}, {"name": "v", "type": "u8", "unit": "$code"}]`)},
		{"PS-374", wave6aRepeatFields(`"index": "i"`, `[{"name": "v", "type": "u8", "ipso": {"object": 3303, "instance": "$i"}}]`)},
		{"PS-373", wave6aRepeatFields("", `[{"name": "v", "type": "u8", "senml": {"name": "t_${nope}"}}]`)},
		{"PS-471", `{"name": "p", "fields": [{"tlv": {"tag_size": 1, "reserve": -1, "cases": {"1": [{"name": "a", "type": "u8"}]}}}]}`},
	}
	for i, c := range cases {
		_, err := ParseSchema(c.schema)
		if err == nil || !strings.Contains(err.Error(), c.tag) {
			t.Errorf("%d %s: got %v", i, c.tag, err)
		}
	}
}

func TestWave6aValidIdentityIsAccepted(t *testing.T) {
	s, err := ParseSchema(wave6aRepeatFields(`"index": "i", "max": 4, "identity": "i"`,
		`[{"name": "code", "type": "u8", "lookup": {"1": "Cel", "2": "%RH"}},
		  {"name": "v", "type": "u8", "unit": "$code", "ipso": {"object": 3303, "instance": "$i"},
		   "senml": {"name": "t_${i}"}}]`))
	if err != nil {
		t.Fatal(err)
	}
	// PS-376: the annotations change nothing about the decode.
	got, err := s.Decode([]byte{0x01, 0x02})
	if err != nil {
		t.Fatal(err)
	}
	want := []any{map[string]any{"code": "Cel", "v": int64(2)}}
	if !reflect.DeepEqual(normalizeInts(got["r"]), normalizeInts(want)) {
		t.Errorf("got %v", got["r"])
	}
}

// normalizeInts widens every integer to int64 so a comparison does not hinge on width.
func normalizeInts(v any) any {
	switch x := v.(type) {
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = normalizeInts(e)
		}
		return out
	case map[string]any:
		out := map[string]any{}
		for k, e := range x {
			out[k] = normalizeInts(e)
		}
		return out
	case int:
		return int64(x)
	case uint64:
		return int64(x)
	case float64:
		if x == float64(int64(x)) {
			return int64(x)
		}
	}
	return v
}

func wave6aDecodeError(t *testing.T, schema string, payload []byte, tag string) {
	t.Helper()
	s, err := ParseSchema(schema)
	if err != nil {
		t.Fatalf("schema rejected: %v", err)
	}
	if _, err := s.Decode(payload); err == nil || !strings.Contains(err.Error(), tag) {
		t.Errorf("want an error citing %s, got %v", tag, err)
	}
}

// PS-351, PS-471: too few bytes for the reserve.
func TestWave6aTooFewBytesForAReserve(t *testing.T) {
	wave6aDecodeError(t, wave6aRepeat(`"reserve": 2`), []byte{0x01}, "PS-351")
	wave6aDecodeError(t, wave6aRepeat(`"reserve": 1, "trailer": [{"name": "t", "type": "u8"}]`),
		[]byte{}, "PS-351")
	wave6aDecodeError(t, `{"name": "p", "fields": [{"tlv": {"tag_size": 1, "reserve": 2, "cases": {"1": [{"name": "a", "type": "u8"}]}}}]}`,
		[]byte{0x01}, "PS-471")
}

// PS-350: the ragged-tail rule applies to the payload less the reserved bytes.
func TestWave6aRaggedTailIsMeasuredAgainstTheRegion(t *testing.T) {
	s, err := ParseSchema(wave6aRepeatFields(`"reserve": 1`, `[{"name": "v", "type": "u16"}]`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Decode([]byte{0x00, 0x01, 0x02}); err != nil {
		t.Errorf("one u16 then one reserved byte: %v", err)
	}
	if _, err := s.Decode([]byte{0x00, 0x01, 0x02, 0x03}); err == nil || !strings.Contains(err.Error(), "ragged") {
		t.Errorf("a u16 and a ragged byte in the region: %v", err)
	}
}

// PS-368: element names are scoped.
func TestWave6aElementScope(t *testing.T) {
	wave6aDecodeError(t, `{"name": "p", "fields": [
		{"name": "r", "type": "repeat", "count": 2, "fields": [{"name": "v", "type": "u8"}]},
		{"name": "last", "type": "number", "ref": "$v"}]}`, []byte{0x01, 0x02}, "PS-368")
	wave6aDecodeError(t, `{"name": "p", "fields": [
		{"name": "r", "type": "repeat", "count": 2, "fields": [{"name": "v", "type": "u8"}]},
		{"name": "sum", "type": "number", "compute": {"op": "add", "a": "$v", "b": 1}}]}`,
		[]byte{0x01, 0x02}, "PS-368")
	wave6aDecodeError(t, `{"name": "p", "fields": [{"name": "r", "type": "repeat", "count": 1, "fields": [
		{"name": "early", "type": "number", "ref": "$v"}, {"name": "v", "type": "u8"}]}]}`,
		[]byte{0x05}, "PS-368")
}

// PS-370: neither the index nor count_as is reported.
func TestWave6aIteratorNamesAreNotReported(t *testing.T) {
	s, err := ParseSchema(wave6aRepeat(`"index": "i", "count_as": "n"`))
	if err != nil {
		t.Fatal(err)
	}
	got, err := s.Decode([]byte{0x01, 0x02})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || len(got["r"].([]any)) != 2 {
		t.Errorf("got %v", got)
	}
}

// PS-378: carry may name a field decoded before the repeat.
func TestWave6aCarryFromAnEarlierField(t *testing.T) {
	s, err := ParseSchema(`{"name": "p", "fields": [
		{"name": "start", "type": "u8"},
		{"name": "r", "type": "repeat", "count": 2, "fields": [
			{"name": "step", "type": "u8"},
			{"name": "level", "type": "number", "carry": "$start",
			 "compute": {"op": "add", "a": "$level", "b": "$step"}}]}]}`)
	if err != nil {
		t.Fatal(err)
	}
	got, err := s.Decode([]byte{0x64, 0x01, 0x05})
	if err != nil {
		t.Fatal(err)
	}
	want := []any{map[string]any{"step": 1, "level": 101}, map[string]any{"step": 5, "level": 106}}
	if !reflect.DeepEqual(normalizeInts(got["r"]), normalizeInts(want)) {
		t.Errorf("got %v", got["r"])
	}
}

// PS-385: the trailer is written after the elements.
func TestWave6aTrailerIsWrittenAfterTheElements(t *testing.T) {
	s, err := ParseSchema(wave6aRepeat(`"reserve": 1, "trailer": [{"name": "base", "type": "u8"}]`))
	if err != nil {
		t.Fatal(err)
	}
	payload, err := s.Encode(map[string]any{
		"r":    []any{map[string]any{"v": 1}, map[string]any{"v": 2}},
		"base": 9,
	})
	if err != nil || !bytes.Equal(payload, []byte{0x01, 0x02, 0x09}) {
		t.Errorf("got % x, %v", payload, err)
	}
	decoded, err := s.Decode([]byte{0x01, 0x02, 0x09})
	if err != nil {
		t.Fatal(err)
	}
	if b, _ := toInt(decoded["base"]); b != 9 || len(decoded["r"].([]any)) != 2 {
		t.Errorf("trailer decode: %v", decoded)
	}
}

// PS-370: a name_from template resolves on encode as it did on decode.
func TestWave6aIndexIsBoundWhileEncoding(t *testing.T) {
	s, err := ParseSchema(`{"name": "p", "fields": [{"name": "r", "type": "repeat", "count": 2,
		"index": "i", "fields": [{"name": "v", "type": "u8", "name_from": "ch_${i}"}]}]}`)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := s.Decode([]byte{0x07, 0x08})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]any{"r": []any{map[string]any{"ch_0": 7}, map[string]any{"ch_1": 8}}}
	if !reflect.DeepEqual(normalizeInts(decoded), normalizeInts(want)) {
		t.Fatalf("got %v", decoded)
	}
	payload, err := s.Encode(decoded)
	if err != nil || !bytes.Equal(payload, []byte{0x07, 0x08}) {
		t.Errorf("got % x, %v", payload, err)
	}
}

// PS-387: present_if over elements that read bytes cannot be encoded.
func TestWave6aPresentIfOverByteReadingElementsCannotBeEncoded(t *testing.T) {
	s, err := ParseSchema(wave6aRepeat(`"present_if": {"field": "$v", "ne": 0}`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Encode(map[string]any{"r": []any{map[string]any{"v": 1}}}); err == nil ||
		!strings.Contains(err.Error(), "PS-387") {
		t.Errorf("want PS-387, got %v", err)
	}
}

// PS-386, PS-367: a dropped element still advances the index and counts toward count;
// count_as counts the elements reported (CR-2026-080).
func TestWave6aPresentIfAndCountAs(t *testing.T) {
	s, err := ParseSchema(`{"name": "p", "fields": [
		{"name": "slots", "type": "repeat", "count": 4, "index": "i", "count_as": "n",
		 "present_if": {"field": "$id", "ne": 0},
		 "fields": [{"name": "id", "type": "u8"}, {"name": "pos", "type": "number", "ref": "$i"}]},
		{"name": "occupied", "type": "number", "ref": "$n"}]}`)
	if err != nil {
		t.Fatal(err)
	}
	got, err := s.Decode([]byte{0x05, 0x00, 0x00, 0x09})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"slots":    []any{map[string]any{"id": 5, "pos": 0}, map[string]any{"id": 9, "pos": 3}},
		"occupied": 2,
	}
	if !reflect.DeepEqual(normalizeInts(got), normalizeInts(want)) {
		t.Errorf("got %v", got)
	}
}
