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

// CR-2026-187 (PS-616 to PS-618): bit_labels reports the labels of a bitmask's set bits, and
// an encoder sets the bits of the labels it is given. The cases are shared with the reference,
// the generated codec, Java and C# in tests/cr-2026-187-cases.yaml.

type cr187Case struct {
	Name     string   `yaml:"name"`
	Schema   string   `yaml:"schema"`
	Payload  string   `yaml:"payload"`
	Input    string   `yaml:"input"`
	Expected any      `yaml:"expected"`
	Warnings []string `yaml:"warnings"`
	Reports  string   `yaml:"reports"`
	Error    string   `yaml:"error"`
}

func cr187Cases(t *testing.T) (decode, encode, meta, load []cr187Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-187-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr187Case `yaml:"decode"`
		Cases  []cr187Case `yaml:"cases"`
		Meta   []cr187Case `yaml:"meta"`
		Load   []cr187Case `yaml:"load"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases, doc.Meta, doc.Load
}

// cr187JSON is a value as JSON reads it back, so a decoded int64 and a YAML int compare.
func cr187JSON(t *testing.T, v any) any {
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal %v: %v", v, err)
	}
	var out any
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	return out
}

func TestCR187DecodeCases(t *testing.T) {
	decode, _, _, _ := cr187Cases(t)
	for _, c := range decode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			payload, _ := hex.DecodeString(c.Payload)
			out, decErr := s.Decode(payload)
			if decErr != nil {
				t.Fatalf("Decode: %v", decErr)
			}
			warnings, _ := out["_warnings"].([]string)
			delete(out, "_warnings")
			if got, want := cr187JSON(t, out), cr187JSON(t, c.Expected); !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %v, want %v", got, want)
			}
			for _, w := range c.Warnings {
				found := false
				for _, g := range warnings {
					found = found || g == w
				}
				if !found {
					t.Errorf("warnings %q lack %q", warnings, w)
				}
			}
			if len(c.Warnings) == 0 && len(warnings) > 0 {
				t.Errorf("unexpected warnings %q", warnings)
			}
		})
	}
}

func TestCR187EncodeCases(t *testing.T) {
	_, encode, _, _ := cr187Cases(t)
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

func TestCR187MetaCases(t *testing.T) {
	_, _, meta, _ := cr187Cases(t)
	for _, c := range meta {
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

func TestCR187LoadCases(t *testing.T) {
	_, _, _, load := cr187Cases(t)
	for _, c := range load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}
