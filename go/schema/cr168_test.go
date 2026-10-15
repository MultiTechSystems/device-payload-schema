package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-168: a definition holds a field group, one field or a lookup table (PS-345); a
// reference to a field may rename it and replace keys whole (PS-578); a field's `lookup` may
// point to a table (PS-579); references resolve transitively (PS-580). The cases are shared
// with the reference, Java and C# in tests/cr-2026-168-cases.yaml.

type cr168Case struct {
	Name      string `yaml:"name"`
	Schema    string `yaml:"schema"`
	Error     string `yaml:"error"`
	FPort     int    `yaml:"fport"`
	Payload   string `yaml:"payload"`
	Expected  string `yaml:"expected"`
	RoundTrip *bool  `yaml:"round_trip"`
	Encode    string `yaml:"encode"`
	Encoded   string `yaml:"encoded"`
}

func TestCR168SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-168-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr168Case `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	for _, c := range doc.Cases {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if c.Error != "" {
				if err == nil || err.Error() != c.Error {
					t.Fatalf("ParseSchema error = %v, want %q", err, c.Error)
				}
				return
			}
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			decode := func(p []byte) (map[string]any, error) {
				if c.FPort != 0 {
					return s.DecodeWithPort(p, c.FPort)
				}
				return s.Decode(p)
			}
			encode := func(d map[string]any) ([]byte, error) {
				if c.FPort != 0 {
					return s.EncodeWithPort(d, c.FPort)
				}
				return s.Encode(d)
			}
			payload, _ := hex.DecodeString(c.Payload)
			decoded, err := decode(payload)
			if err != nil {
				t.Fatalf("decode: %v", err)
			}
			var expected map[string]any
			if err := json.Unmarshal([]byte(c.Expected), &expected); err != nil {
				t.Fatalf("expected: %v", err)
			}
			delete(decoded, "_quality") // `valid_range` reports one beside the values
			if !reflect.DeepEqual(cr159Normalize(decoded), cr159Normalize(expected)) {
				t.Fatalf("Decode = %v, want %v", decoded, expected)
			}
			if c.RoundTrip == nil || *c.RoundTrip {
				out, err := encode(decoded)
				if err != nil {
					t.Fatalf("encode: %v", err)
				}
				if got := hex.EncodeToString(out); got != c.Payload {
					t.Fatalf("Encode = %s, want %s", got, c.Payload)
				}
			}
			if c.Encode != "" {
				var given map[string]any
				if err := json.Unmarshal([]byte(c.Encode), &given); err != nil {
					t.Fatalf("encode input: %v", err)
				}
				out, err := encode(given)
				if err != nil {
					t.Fatalf("encode %v: %v", given, err)
				}
				if got := hex.EncodeToString(out); got != c.Encoded {
					t.Fatalf("Encode(%v) = %s, want %s", given, got, c.Encoded)
				}
			}
		})
	}
}

// A resolved template keeps its source key order, which a match's case order reads: with
// two cases equally given, the one declared first is written (PS-563), here case 2.
func TestCR168TemplateKeepsKeyOrder(t *testing.T) {
	s, err := ParseSchema("name: t\ndefinitions:\n  f:\n    field:\n      type: object\n" +
		"      fields:\n        - match:\n            length: 1\n            cases:\n" +
		"              2: [{name: a, type: u8}]\n              1: [{name: a, type: u8}]\n" +
		"fields:\n  - {$ref: '#/definitions/f', name: o}\n")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	out, err := s.Encode(map[string]any{"o": map[string]any{"a": 5}})
	if err != nil || hex.EncodeToString(out) != "0205" {
		t.Fatalf("Encode = %x, %v; want 0205", out, err)
	}
}

// A document only JSON reads resolves its references the same way.
func TestCR168JSONDocument(t *testing.T) {
	s, err := ParseSchema(`{"name": "t", "definitions": {"c": {"lookup": ["off", "on"]}},
		"fields": [{"name": "s", "type": "u8", "lookup": "#/definitions/c"}]}`)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	out, err := s.Decode([]byte{1})
	if err != nil || out["s"] != "on" {
		t.Fatalf("Decode = %v, %v", out, err)
	}
}
