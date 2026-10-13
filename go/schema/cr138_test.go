package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-138 (PS-553): a field has a name; padding is a skip. A schema with a nameless
// field other than a skip is refused at load, naming the field by its position in the
// spec prototype's text; constructs, byte_group members and a repeat's element stay
// nameless. The cases are shared with the reference, Java and C# in
// tests/cr-2026-138-cases.yaml.

type cr138Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Error    string `yaml:"error"`
	Payload  string `yaml:"payload"`
	Expected string `yaml:"expected"`
}

// cr138Normalize compares numbers by value, so a decoded int equals a parsed float64.
func cr138Normalize(v any) any {
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, val := range x {
			out[k] = cr138Normalize(val)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, val := range x {
			out[i] = cr138Normalize(val)
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

func TestCR138SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-138-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr138Case `yaml:"cases"`
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
			if !reflect.DeepEqual(cr138Normalize(decoded), cr138Normalize(expected)) {
				t.Fatalf("Decode = %v, want %v", decoded, expected)
			}
			out, err := s.Encode(decoded)
			if err != nil {
				t.Fatalf("encode: %v", err)
			}
			if got := hex.EncodeToString(out); got != c.Payload {
				t.Fatalf("Encode = %s, want %s", got, c.Payload)
			}
		})
	}
}
