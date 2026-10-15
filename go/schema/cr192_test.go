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

// CR-2026-192 (PS-567, PS-569, PS-615, PS-629): time fields and the Integration Layer. The
// cases are shared with the reference, the generated codec, Java and C# in
// tests/cr-2026-192-cases.yaml.

type cr192Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Payload  string `yaml:"payload"`
	Input    string `yaml:"input"`
	Expected any    `yaml:"expected"`
	Error    string `yaml:"error"`
}

type cr192Doc struct {
	Decode []cr192Case `yaml:"decode"`
	Cases  []cr192Case `yaml:"cases"`
	Load   []cr192Case `yaml:"load"`
	Meta   []cr192Case `yaml:"meta"`
}

func cr192Cases(t *testing.T) cr192Doc {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-192-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc cr192Doc
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Decode) == 0 ||
		len(doc.Cases) == 0 || len(doc.Load) == 0 || len(doc.Meta) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc
}

func cr192JSON(t *testing.T, v any) any {
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal %v: %v", v, err)
	}
	var out any
	_ = json.Unmarshal(raw, &out)
	return out
}

func TestCR192DecodeCases(t *testing.T) {
	for _, c := range cr192Cases(t).Decode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			payload, _ := hex.DecodeString(c.Payload)
			out, err := s.Decode(payload)
			if err != nil {
				t.Fatalf("Decode: %v", err)
			}
			delete(out, "_warnings")
			delete(out, "_quality")
			if got, want := cr192JSON(t, out), cr192JSON(t, c.Expected); !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %v, want %v", got, want)
			}
		})
	}
}

func TestCR192EncodeCases(t *testing.T) {
	for _, c := range cr192Cases(t).Cases {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			var data map[string]any
			if err := json.Unmarshal([]byte(c.Input), &data); err != nil {
				t.Fatalf("input: %v", err)
			}
			for _, ordered := range []bool{false, true} {
				var out []byte
				var encErr error
				if ordered {
					out, encErr = s.EncodeOrdered(data, nil)
				} else {
					out, encErr = s.Encode(data)
				}
				if encErr != nil {
					t.Fatalf("Encode (ordered %v): %v", ordered, encErr)
				}
				if got := hex.EncodeToString(out); got != c.Payload {
					t.Errorf("Encode (ordered %v) = %s, want %s", ordered, got, c.Payload)
				}
			}
		})
	}
}

func TestCR192LoadCases(t *testing.T) {
	for _, c := range cr192Cases(t).Load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}

func TestCR192MetaCases(t *testing.T) {
	for _, c := range cr192Cases(t).Meta {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			payload, _ := hex.DecodeString(c.Payload)
			out, err := s.Interpret(payload, InputContext{})
			if err != nil {
				t.Fatalf("Interpret: %v", err)
			}
			fields := cr192JSON(t, out["_meta"]).(map[string]any)["fields"].(map[string]any)
			for name, keys := range cr192JSON(t, c.Expected).(map[string]any) {
				entry, _ := fields[name].(map[string]any)
				for key, want := range keys.(map[string]any) {
					got, present := entry[key]
					if want == nil {
						if present {
							t.Errorf("%s.%s = %v, want absent", name, key, got)
						}
					} else if !reflect.DeepEqual(got, want) {
						t.Errorf("%s.%s = %v, want %v", name, key, got, want)
					}
				}
			}
		})
	}
}
