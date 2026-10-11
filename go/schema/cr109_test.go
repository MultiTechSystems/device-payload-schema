package schema

import (
	"bytes"
	"reflect"
	"strings"
	"testing"
)

// CR-2026-109 (PS-523, PS-524): a reference to a looked-up field binds the number under a
// text or boolean label, or a numeric label as itself, and survives a lookup miss; a match
// selects on it; a name_from template still writes the label (PS-532).

func cr109Decode(t *testing.T, yaml string, payload []byte) (map[string]any, error) {
	t.Helper()
	s, err := ParseSchema(yaml)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	return s.Decode(payload)
}

// cr109Schema puts the four reference sites after one looked-up field.
func cr109Schema(lookup, caseKey string) string {
	return `
name: cr109
endian: big
fields:
  - {name: code, type: u8, var: c, lookup: ` + lookup + `}
  - {name: doubled, type: number, compute: {op: mul, a: $code, b: 2}}
  - {name: held, type: number, ref: $c}
  - match:
      field: $code
      cases:
        ` + caseKey + `: [{name: reading, type: u8, name_from: "reading_${code}"}]
      default: [{name: other, type: u8}]
`
}

func TestCR109ReferenceSitesPerLookupKind(t *testing.T) {
	cases := []struct {
		name, lookup, key string
		payload           []byte
		want              map[string]any
	}{
		{"text label", "{1: report, 129: report_rsp}", "129", []byte{0x81, 7},
			map[string]any{"code": "report_rsp", "doubled": 258.0, "held": 129.0, "reading_report_rsp": 7}},
		{"boolean label", "{0: false, 1: true}", "1", []byte{0x01, 7},
			map[string]any{"code": true, "doubled": 2.0, "held": 1.0, "reading_true": 7}},
		{"numeric sequence label", "[0, 125, 250, 500]", "125", []byte{0x01, 7},
			map[string]any{"code": 125, "doubled": 250.0, "held": 125.0, "reading_125": 7}},
		{"numeric mapping label", "{1: 10, 2: 20}", "20", []byte{0x02, 7},
			map[string]any{"code": 20, "doubled": 40.0, "held": 20.0, "reading_20": 7}},
		{"default label", "{1: report, default: spare}", "5", []byte{0x05, 7},
			map[string]any{"code": "spare", "doubled": 10.0, "held": 5.0, "reading_spare": 7}},
		{"templated default", `{1: report, default: "code_${value}"}`, "5", []byte{0x05, 7},
			map[string]any{"code": "code_5", "doubled": 10.0, "held": 5.0, "reading_code_5": 7}},
	}
	for _, c := range cases {
		out, err := cr109Decode(t, cr109Schema(c.lookup, c.key), c.payload)
		if err != nil {
			t.Errorf("%s: decode: %v", c.name, err)
			continue
		}
		if len(out) != len(c.want) {
			t.Errorf("%s: got %#v, want %#v", c.name, out, c.want)
			continue
		}
		for k, w := range c.want {
			got, ok := out[k]
			gf, gok := toFloat64(got)
			wf, wok := toFloat64(w)
			if !ok || (gok && wok && gf != wf) || (!(gok && wok) && !reflect.DeepEqual(got, w)) {
				t.Errorf("%s: %s = %#v, want %#v (all: %#v)", c.name, k, got, w, out)
			}
		}
	}
}

func TestCR109MissOmitsTheFieldButBindsTheNumber(t *testing.T) {
	out, err := cr109Decode(t, `
name: cr109
endian: big
fields:
  - {name: code, type: u8, var: c, lookup: {1: report, 129: report_rsp}}
  - {name: doubled, type: number, compute: {op: mul, a: $code, b: 2}}
  - {name: held, type: number, ref: $c}
  - match:
      field: $code
      cases:
        5: [{name: unlisted, type: u8}]
      default: [{name: other, type: u8}]
`, []byte{0x05, 7})
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, reported := out["code"]; reported {
		t.Errorf("a miss must not be reported: %#v", out)
	}
	if out["doubled"] != 10.0 || out["held"] != 5.0 || out["unlisted"] == nil {
		t.Errorf("got %#v, want doubled 10, held 5, unlisted 7", out)
	}
}

func TestCR109TemplateOverAMissIsAnError(t *testing.T) {
	_, err := cr109Decode(t, `
name: cr109
endian: big
fields:
  - {name: code, type: u8, lookup: {1: report}}
  - {name: x, type: u8, name_from: "r_${code}"}
`, []byte{0x05, 7})
	if err == nil || !strings.Contains(err.Error(), "which reported no value (PS-266, PS-532)") {
		t.Fatalf("expected the PS-532 error, got %v", err)
	}
}

func TestCR109SentinelLeavesTheReferenceUnbound(t *testing.T) {
	out, err := cr109Decode(t, `
name: cr109
endian: big
fields:
  - {name: code, type: u8, sentinel: [255], lookup: {1: report}}
  - {name: doubled, type: number, compute: {op: mul, a: $code, b: 2}}
`, []byte{0xFF})
	// Go reports an unbound reference as an error; either way nothing is computed.
	if err == nil && out["doubled"] != nil {
		t.Fatalf("a reference to a field a sentinel omitted must be unbound: %#v", out)
	}
}

func TestCR109NumericDefaultLabelBindsTheLabel(t *testing.T) {
	out, err := cr109Decode(t, `
name: cr109
endian: big
fields:
  - {name: size, type: u8, lookup: {1: 10, default: 0}}
  - {name: doubled, type: number, compute: {op: add, a: $size, b: 1}}
`, []byte{0x05})
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out["doubled"] != 1.0 {
		t.Errorf("doubled = %#v, want 1 (the numeric default 0, plus 1)", out["doubled"])
	}
}

func TestCR109ByteGroupMemberAndTLVCase(t *testing.T) {
	out, err := cr109Decode(t, `
name: cr109
endian: big
fields:
  - byte_group:
      size: 1
      fields:
        - {name: mode, type: "u8[0:3]", lookup: {2: eco, 3: boost}}
  - {name: m, type: number, ref: $mode}
  - tlv:
      tag_size: 1
      cases:
        1:
          - {name: kind, type: u8, lookup: {7: seven}}
          - {name: k, type: number, ref: $kind}
`, []byte{0x03, 0x01, 0x07})
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out["mode"] != "boost" || out["m"] != 3.0 || out["kind"] != "seven" || out["k"] != 7.0 {
		t.Errorf("got %#v", out)
	}
}

func TestCR109EncodeSelectsTheCaseFromTheLabel(t *testing.T) {
	s, err := ParseSchema(`
name: cr109
endian: big
fields:
  - {name: code, type: u8, lookup: {1: report, 129: report_rsp}}
  - match:
      field: $code
      cases:
        129: [{name: status, type: u8}]
      default: [{name: other, type: u8}]
`)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	payload := []byte{0x81, 0x07}
	out, err := s.Decode(payload)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	back, err := s.Encode(out)
	if err != nil || !bytes.Equal(back, payload) {
		t.Fatalf("encode(%#v) = %x, %v; want %x", out, back, err, payload)
	}
}

// CR-2026-132: an enum is a lookup by another spelling. A reference binds its code, its
// `default` name included, and a template writes its name.
func TestCR132EnumBindsItsCode(t *testing.T) {
	cases := []struct {
		name, values, key string
		payload           []byte
		want              map[string]any
	}{
		{"mapped", `{1: "on", 2: "off"}`, "2", []byte{2, 7},
			map[string]any{"code": "off", "doubled": 4.0, "held": 2.0, "reading_off": 7}},
		{"default", `{1: "on", 2: "off"}, default: spare`, "5", []byte{5, 7},
			map[string]any{"code": "spare", "doubled": 10.0, "held": 5.0, "reading_spare": 7}},
	}
	for _, c := range cases {
		out, err := cr109Decode(t, `
name: cr132
endian: big
fields:
  - {name: code, type: enum, base: u8, var: c, values: `+c.values+`}
  - {name: doubled, type: number, compute: {op: mul, a: $code, b: 2}}
  - {name: held, type: number, ref: $c}
  - match:
      field: $code
      cases:
        `+c.key+`: [{name: reading, type: u8, name_from: "reading_${code}"}]
      default: [{name: other, type: u8}]
`, c.payload)
		if err != nil {
			t.Errorf("%s: decode: %v", c.name, err)
			continue
		}
		if len(out) != len(c.want) {
			t.Errorf("%s: got %#v, want %#v", c.name, out, c.want)
			continue
		}
		for k, w := range c.want {
			got := out[k]
			gf, gok := toFloat64(got)
			wf, wok := toFloat64(w)
			if (gok && wok && gf != wf) || (!(gok && wok) && !reflect.DeepEqual(got, w)) {
				t.Errorf("%s: %s = %#v, want %#v", c.name, k, got, w)
			}
		}
	}
}

func TestCR132EnumEncodeSelectsTheCaseFromItsName(t *testing.T) {
	s, err := ParseSchema(`
name: cr132
endian: big
fields:
  - {name: code, type: enum, base: u8, values: {1: "on", 2: "off"}}
  - match:
      field: $code
      cases:
        2: [{name: status, type: u8}]
      default: [{name: other, type: u8}]
`)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	payload := []byte{0x02, 0x07}
	out, err := s.Decode(payload)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out["status"] == nil {
		t.Fatalf("the case keyed by the code was not selected: %#v", out)
	}
	back, err := s.Encode(out)
	if err != nil || !bytes.Equal(back, payload) {
		t.Fatalf("encode(%#v) = %x, %v; want %x", out, back, err, payload)
	}
}

// CR-2026-132: a tag component's name and `var` bind as any field's do.
func TestCR132TagComponentBindsAsAField(t *testing.T) {
	for _, c := range []struct {
		lookup, key string
		payload     []byte
		reading     string
		doubled     float64
	}{
		{"{1: left, 2: right}", "2", []byte{2, 0x10, 7, 5}, "reading_right", 4},
		{"[0, 125, 250]", "125", []byte{1, 0x10, 7, 5}, "reading_125", 250},
	} {
		out, err := cr109Decode(t, `
name: cr132
endian: big
fields:
  - tlv:
      tag_fields:
        - {name: side, type: u8, var: s, lookup: `+c.lookup+`}
        - {name: kind, type: u8}
      tag_key: [side, kind]
      cases:
        "[*, 0x10]":
          - {name: reading, type: u8, name_from: "reading_${s}"}
          - {name: doubled, type: number, compute: {op: mul, a: $side, b: 2}}
          - match:
              field: $s
              cases:
                `+c.key+`: [{name: hit, type: u8}]
              default: [{name: other, type: u8}]
`, c.payload)
		if err != nil {
			t.Errorf("%s: decode: %v", c.lookup, err)
			continue
		}
		d, _ := toFloat64(out["doubled"])
		if out[c.reading] == nil || d != c.doubled || out["hit"] == nil || out["other"] != nil {
			t.Errorf("%s: got %#v", c.lookup, out)
		}
	}
}
