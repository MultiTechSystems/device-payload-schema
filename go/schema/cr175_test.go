package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-175 (PS-453, PS-015, PS-588, PS-589): a bytes, hex or ascii field declaring
// `consume: 0` reports its span and leaves the read position; an encoder writes nothing
// for it and checks a value given for it. The cases are shared with the reference, the
// generated codec, Java and C# in tests/cr-2026-175-cases.yaml.

type cr175Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Payload  string `yaml:"payload"`
	Input    string `yaml:"input"`
	Expected string `yaml:"expected"`
	Error    string `yaml:"error"`
}

func cr175Cases(t *testing.T) (decode, encode []cr175Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-175-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr175Case `yaml:"decode"`
		Cases  []cr175Case `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 || len(doc.Decode) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases
}

// cr175Normal brings decoded output to JSON's shapes, so it compares with the expected
// JSON text: numbers as float64, nested values member by member.
func cr175Normal(v any) any {
	raw, err := json.Marshal(v)
	if err != nil {
		return v
	}
	var out any
	_ = json.Unmarshal(raw, &out)
	return out
}

func TestCR175DecodeCases(t *testing.T) {
	decode, _ := cr175Cases(t)
	for _, c := range decode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			payload, _ := hex.DecodeString(c.Payload)
			out, decErr := s.Decode(payload)
			if c.Error != "" {
				if decErr == nil || !strings.Contains(decErr.Error(), c.Error) {
					t.Errorf("Decode = %v, %v; want error %q", out, decErr, c.Error)
				}
				return
			}
			if decErr != nil {
				t.Fatalf("Decode: %v", decErr)
			}
			var want any
			if err := json.Unmarshal([]byte(c.Expected), &want); err != nil {
				t.Fatalf("expected: %v", err)
			}
			if got := cr175Normal(out); !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %v, want %v", got, want)
			}
		})
	}
}

func TestCR175EncodeCases(t *testing.T) {
	_, encode := cr175Cases(t)
	for _, c := range encode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			var data map[string]any
			if err := json.Unmarshal([]byte(c.Input), &data); err != nil {
				t.Fatalf("input: %v", err)
			}
			out, encErr := s.Encode(data)
			if c.Error != "" {
				if encErr == nil || !strings.Contains(encErr.Error(), c.Error) {
					t.Errorf("Encode = %x, %v; want error %q", out, encErr, c.Error)
				}
				if len(out) != 0 {
					t.Errorf("Encode returned payload %x with its error", out)
				}
				return
			}
			if encErr != nil {
				t.Fatalf("Encode: %v", encErr)
			}
			if got := hex.EncodeToString(out); got != c.Payload {
				t.Errorf("Encode = %s, want %s", got, c.Payload)
			}
		})
	}
}
