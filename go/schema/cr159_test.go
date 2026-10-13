package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-159: `formula`, `encode_formula`, a transform's `clamp`, a port's `confirmed`, and
// `min`/`max` on a field that is no repeat are refused at load, each in the spec
// prototype's text and naming what replaces it. `ties: even`, a repeat's `min`/`max` and a
// `valid_range` stay valid. The cases are shared with the reference, Java and C# in
// tests/cr-2026-159-cases.yaml.

type cr159Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Error    string `yaml:"error"`
	Payload  string `yaml:"payload"`
	Expected string `yaml:"expected"`
	Encode   string `yaml:"encode"`
	Encoded  string `yaml:"encoded"`
}

// cr159Normalize compares numbers by value, so a decoded int equals a parsed float64.
func cr159Normalize(v any) any {
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, val := range x {
			out[k] = cr159Normalize(val)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, val := range x {
			out[i] = cr159Normalize(val)
		}
		return out
	}
	if f, ok := toFloat64(v); ok {
		if _, isBool := v.(bool); !isBool {
			return f
		}
	}
	return v
}

func TestCR159SharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "tests", "cr-2026-159-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr159Case `yaml:"cases"`
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
			payload, _ := hex.DecodeString(c.Payload)
			decoded, err := s.Decode(payload)
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
			out, err := s.Encode(decoded)
			if err != nil {
				t.Fatalf("encode: %v", err)
			}
			if got := hex.EncodeToString(out); got != c.Payload {
				t.Fatalf("Encode = %s, want %s", got, c.Payload)
			}
			if c.Encode != "" {
				var given map[string]any
				if err := json.Unmarshal([]byte(c.Encode), &given); err != nil {
					t.Fatalf("encode input: %v", err)
				}
				out, err := s.Encode(given)
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
