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

// CR-2026-186 (PS-610 to PS-615): a field's `time`. The cases are shared with the
// reference, the generated codec, Java and C# in tests/cr-2026-186-cases.yaml.

type cr186Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Payload  string `yaml:"payload"`
	Input    string `yaml:"input"`
	Expected any    `yaml:"expected"`
	Error    string `yaml:"error"`
	Reports  string `yaml:"reports"`
}

type cr186Doc struct {
	Decode []cr186Case `yaml:"decode"`
	Cases  []cr186Case `yaml:"cases"`
	Load   []cr186Case `yaml:"load"`
	Meta   []cr186Case `yaml:"meta"`
}

func cr186Cases(t *testing.T) cr186Doc {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-186-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc cr186Doc
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Decode) == 0 ||
		len(doc.Cases) == 0 || len(doc.Load) == 0 || len(doc.Meta) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc
}

func cr186JSON(t *testing.T, v any) any {
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal %v: %v", v, err)
	}
	var out any
	_ = json.Unmarshal(raw, &out)
	return out
}

func TestCR186DecodeCases(t *testing.T) {
	for _, c := range cr186Cases(t).Decode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			payload, _ := hex.DecodeString(c.Payload)
			out, decErr := s.Decode(payload)
			if c.Error != "" {
				if decErr == nil || !strings.Contains(decErr.Error(), c.Error) {
					t.Fatalf("Decode error = %v; want %q (output %v)", decErr, c.Error, out)
				}
				return
			}
			if decErr != nil {
				t.Fatalf("Decode: %v", decErr)
			}
			delete(out, "_warnings")
			delete(out, "_quality")
			if got, want := cr186JSON(t, out), cr186JSON(t, c.Expected); !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %v, want %v", got, want)
			}
		})
	}
}

func TestCR186EncodeCases(t *testing.T) {
	for _, c := range cr186Cases(t).Cases {
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
				if c.Error != "" {
					if encErr == nil || !strings.Contains(encErr.Error(), c.Error) {
						t.Errorf("Encode (ordered %v) error = %v; want %q (payload %x)",
							ordered, encErr, c.Error, out)
					}
					continue
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

func TestCR186LoadCases(t *testing.T) {
	for _, c := range cr186Cases(t).Load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}

func TestCR186MetaCases(t *testing.T) {
	for _, c := range cr186Cases(t).Meta {
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
			entry := out["_meta"].(map[string]any)["fields"].(map[string]any)["f"].(map[string]any)
			if entry["reports"] != c.Reports {
				t.Errorf("reports = %v, want %s", entry["reports"], c.Reports)
			}
		})
	}
}

// A reference to a time field binds the number, not the text it reports (PS-610).
func TestCR186ReferenceBindsTheNumber(t *testing.T) {
	s, err := ParseSchema("name: t\nendian: big\nfields:\n" +
		"  - {name: t, type: u16, time: {format: iso8601}}\n" +
		"  - {name: twice, type: number, ref: $t, mult: 2}\n")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	out, err := s.Decode([]byte{0x00, 0x05})
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	if out["t"] != "1970-01-01T00:00:05Z" || cr186JSON(t, out["twice"]) != 10.0 {
		t.Errorf("Decode = %v", out)
	}
}
