// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"encoding/hex"
	"strings"
	"testing"
)

// CR-2026-071 (a field's arithmetic pipeline) and CR-2026-073 (one operation per stage).
//
// The decode order itself - source, modifiers, transform, lookup, with a failed guard's
// `else` reported as declared - is held to every implementation by three corpus fixtures:
// _language-conformance/arithmetic-order-computed.yaml, arithmetic-order-read.yaml and
// guard-else-as-declared.yaml. What a fixture cannot state is a schema that must be
// refused, so the rejections are here, mirroring tests/test_cr_2026_071_073.py.

const cr071Guard = "{when: [{field: $x, gt: 0}], else: 0}"

func cr071Schema(fields ...string) string {
	return "name: probe\nfields:\n  - {name: x, type: u8, var: x}\n" + strings.Join(fields, "")
}

func cr071AssertRejected(t *testing.T, label, yaml, tag string, also ...string) {
	t.Helper()
	_, err := ParseSchema(yaml)
	if err == nil {
		t.Errorf("%s: accepted", label)
		return
	}
	for _, want := range append([]string{tag}, also...) {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("%s: error %q does not mention %q", label, err, want)
		}
	}
}

func cr071Decode(t *testing.T, yaml, payload string) (map[string]any, error) {
	t.Helper()
	s, err := ParseSchema(yaml)
	if err != nil {
		t.Fatalf("ParseSchema: %v", err)
	}
	data, _ := hex.DecodeString(payload)
	return s.Decode(data)
}

// PS-445: a guard belongs to a computed field. Go ignored it on any other with success.
func TestCR071GuardOffAComputedFieldIsRejected(t *testing.T) {
	for label, field := range map[string]string{
		"u8":      "  - {name: v, type: u8, guard: " + cr071Guard + "}\n",
		"s16":     "  - {name: v, type: s16, guard: " + cr071Guard + "}\n",
		"literal": "  - {name: v, type: number, value: 3, guard: " + cr071Guard + "}\n",
	} {
		cr071AssertRejected(t, label, cr071Schema(field), "PS-445")
	}
}

func TestCR071GuardOnAComputedFieldIsAccepted(t *testing.T) {
	for label, source := range map[string]string{
		"ref":     "ref: $x",
		"compute": "compute: {op: add, a: $x, b: 1}",
		"integer": "type: integer, ref: $x",
	} {
		typ := "type: number, "
		if label == "integer" {
			typ = ""
		}
		yaml := cr071Schema("  - {name: v, " + typ + source + ", guard: " + cr071Guard + "}\n")
		if _, err := cr071Decode(t, yaml, "03"); err != nil {
			t.Errorf("%s: %v", label, err)
		}
	}
}

// PS-452: exactly one operation per stage. Go applied every operation a stage held, mult
// before add, so {add: 1, mult: 2} decoded raw 1 as 3.
func TestCR073StageWithoutExactlyOneOperationIsRejected(t *testing.T) {
	for stage, held := range map[string]string{
		"{add: 1, mult: 2}":        "holds add, mult",
		"{div: 2, sqrt: true}":     "holds div, sqrt",
		"{op: round, add: 1}":      "holds add, op",
		"{}":                       "holds none",
		"{decimals: 2}":            "holds none",
		"{ties: away}":             "holds none",
		"{log: true, log10: true}": "holds log, log10",
	} {
		yaml := "name: probe\nfields:\n  - {name: v, type: u8, transform: [" + stage + "]}\n"
		cr071AssertRejected(t, stage, yaml, "PS-452", held)
	}
}

// `decimals` and `ties` are parameters of an `op:` stage, and an error anywhere else.
func TestCR073OpParametersArePartOfItsOneOperation(t *testing.T) {
	out, err := cr071Decode(t, "name: probe\nfields:\n"+
		"  - {name: v, type: u8, transform: [{op: round, decimals: 1, ties: away}, {add: 1}]}\n", "03")
	if err != nil || mustNum(out["v"]) != 4 {
		t.Errorf("got %v, %v; want v=4", out, err)
	}
	cr071AssertRejected(t, "decimals on add",
		"name: probe\nfields:\n  - {name: v, type: u8, transform: [{add: 1, decimals: 2}]}\n",
		"PS-452", "belong to an `op:` stage")
}

// An unknown key beside a real operation is still PS-390. `{add: 1, sub: 2}` passed the
// old "names some operation" check on its `add`, and the `sub` was applied too.
func TestCR073UnknownKeyBesideAnOperationIsRejected(t *testing.T) {
	cr071AssertRejected(t, "add+sub",
		"name: probe\nfields:\n  - {name: v, type: u8, transform: [{add: 1, sub: 2}]}\n",
		"PS-390", "sub")
}

// Both rules hold wherever the field sits, not only at the top level.
func TestCR071073CheckedWhereverTheFieldSits(t *testing.T) {
	twoOps := "transform: [{add: 1, div: 2}]"
	guard := "guard: " + cr071Guard
	for label, wrap := range map[string]func(string) string{
		"byte_group": func(extra string) string {
			return "name: p\nfields:\n  - byte_group: {size: 1, fields: [" +
				"{name: hi, type: 'u8[4:7]', " + extra + "}, {name: lo, type: 'u8[0:3]', consume: 1}]}\n"
		},
		"match case": func(extra string) string {
			return "name: p\nfields:\n  - {name: k, type: u8, var: k}\n" +
				"  - match: {field: $k, cases: {1: [{name: a, type: u8, " + extra + "}]}}\n"
		},
		"tlv case": func(extra string) string {
			return "name: p\nfields:\n  - tlv: {tag_size: 1, cases: {1: [{name: a, type: u8, " + extra + "}]}}\n"
		},
		"flagged group": func(extra string) string {
			return "name: p\nfields:\n  - {name: f, type: u8, var: f}\n" +
				"  - flagged: {field: f, groups: [{bit: 0, fields: [{name: a, type: u8, " + extra + "}]}]}\n"
		},
		"repeat element": func(extra string) string {
			return "name: p\nfields:\n  - {name: r, type: repeat, count: 2, fields: [{name: a, type: u8, " + extra + "}]}\n"
		},
		"object": func(extra string) string {
			return "name: p\nfields:\n  - {name: o, type: object, fields: [{name: a, type: u8, " + extra + "}]}\n"
		},
	} {
		cr071AssertRejected(t, label+" stage", wrap(twoOps), "PS-452")
		cr071AssertRejected(t, label+" guard", wrap(guard), "PS-445")
	}
}

// PS-443: a compute takes the bare modifiers as a ref does. They were ignored with
// success: `mult: 10` beside a compute reported the unscaled 2.
func TestCR071ComputeTakesTheModifiers(t *testing.T) {
	out, err := cr071Decode(t, cr071Schema(
		"  - {name: v, type: number, compute: {op: add, a: $x, b: 0}, mult: 10, transform: [{add: 1}]}\n"), "02")
	if err != nil || mustNum(out["v"]) != 21 {
		t.Errorf("got %v, %v; want v=21", out, err)
	}
}

// PS-444: a failed guard's `else` is the value as declared - not passed through the
// lookup, which reported `else: 7` as the eighth label, nor through the arithmetic.
func TestCR071GuardElseIsTheValueAsDeclared(t *testing.T) {
	yaml := cr071Schema(
		"  - {name: looked, type: number, ref: $x, guard: {when: [{field: $x, gt: 0}], else: 7}, lookup: [a, b, c, d, e, f, g, h]}\n",
		"  - {name: scaled, type: number, ref: $x, guard: {when: [{field: $x, gt: 0}], else: 5}, mult: 10, transform: [{add: 1}]}\n",
		"  - {name: absent, type: number, compute: {op: add, a: $x, b: 0}, guard: {when: [{field: $x, gt: 0}]}, mult: 10}\n")
	out, err := cr071Decode(t, yaml, "00")
	if err != nil {
		t.Fatal(err)
	}
	if mustNum(out["looked"]) != 7 || mustNum(out["scaled"]) != 5 {
		t.Errorf("got %v; want looked=7 scaled=5", out)
	}
	if _, present := out["absent"]; present {
		t.Errorf("a failed guard with no else reported %v; want the field absent (PS-400)", out["absent"])
	}
	out, err = cr071Decode(t, yaml, "02")
	if err != nil || out["looked"] != "c" || mustNum(out["scaled"]) != 21 || mustNum(out["absent"]) != 20 {
		t.Errorf("guard passing: got %v, %v; want looked=c scaled=21 absent=20", out, err)
	}
}

// PS-105: a computed value reaches the lookup as a float. An integral one is its index;
// one with a fraction is no index of a sequence, an error. toInt truncated 1.5 to 1.
func TestCR071FractionalValueIndexesNoEntryOfASequence(t *testing.T) {
	yaml := cr071Schema("  - {name: v, type: number, ref: $x, div: 2, lookup: [a, b, c, d]}\n")
	if out, err := cr071Decode(t, yaml, "04"); err != nil || out["v"] != "c" {
		t.Errorf("x=4: got %v, %v; want v=c", out, err)
	}
	if out, err := cr071Decode(t, yaml, "03"); err == nil || !strings.Contains(err.Error(), "PS-105") {
		t.Errorf("x=3: got %v, %v; want a PS-105 error", out, err)
	}
}

// For a mapping a fraction matches no key: omitted, or the `default`.
func TestCR071FractionalValueMatchesNoKeyOfAMapping(t *testing.T) {
	out, err := cr071Decode(t, cr071Schema(
		"  - {name: v, type: number, ref: $x, div: 2, lookup: {1: one, 2: two}}\n",
		"  - {name: w, type: number, ref: $x, div: 2, lookup: {1: one, default: other}}\n"), "03")
	if err != nil {
		t.Fatal(err)
	}
	if _, present := out["v"]; present || out["w"] != "other" {
		t.Errorf("got %v; want v absent and w=other", out)
	}
}

// Encoding undoes the stages last-first and then the bare modifiers. With a transform
// present the encoder undid only the stages, so 50 encoded as 0x0041 (65), the `div: 10`
// never undone.
func TestCR071EncodeUndoesTheModifiersBesideATransform(t *testing.T) {
	s, err := ParseSchema("name: p\nendian: big\nfields:\n" +
		"  - {name: v, type: u16, div: 10, transform: [{add: -40}, {mult: 2}]}\n")
	if err != nil {
		t.Fatal(err)
	}
	out, err := s.Encode(map[string]any{"v": 50.0})
	if err != nil || hex.EncodeToString(out) != "028a" {
		t.Errorf("got %x, %v; want 028a", out, err)
	}
}

// A stage that does not invert is reported, not written back as the decoded value.
func TestCR071EncodeReportsAStageThatDoesNotInvert(t *testing.T) {
	s, err := ParseSchema("name: p\nfields:\n  - {name: v, type: u16, transform: [{sqrt: true}]}\n")
	if err != nil {
		t.Fatal(err)
	}
	if out, err := s.Encode(map[string]any{"v": 20.0}); err == nil {
		t.Errorf("sqrt stage encoded as %x with no error", out)
	}
}
