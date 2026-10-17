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

// CR-2026-193 (PS-630, PS-631): a tlv entry with a length bounds its case. The cases are
// shared with the reference, the generated codec, Java and C# in tests/cr-2026-193-cases.yaml.

type cr193Case struct {
	Name     string   `yaml:"name"`
	Schema   string   `yaml:"schema"`
	Payload  string   `yaml:"payload"`
	Input    string   `yaml:"input"`
	Expected any      `yaml:"expected"`
	Warnings []string `yaml:"warnings"`
	Error    string   `yaml:"error"`
}

func cr193Cases(t *testing.T) (decode, encode []cr193Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-193-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr193Case `yaml:"decode"`
		Cases  []cr193Case `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Decode) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases
}

func TestCR193DecodeCases(t *testing.T) {
	decode, _ := cr193Cases(t)
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
			warnings, _ := out["_warnings"].([]string)
			delete(out, "_warnings")
			if got, want := cr187JSON(t, out), cr187JSON(t, c.Expected); !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %v, want %v", got, want)
			}
			if len(warnings) != len(c.Warnings) {
				t.Fatalf("warnings %q, want %q", warnings, c.Warnings)
			}
			for i := range warnings {
				if warnings[i] != c.Warnings[i] {
					t.Errorf("warnings %q, want %q", warnings, c.Warnings)
				}
			}
		})
	}
}

func TestCR193EncodeCases(t *testing.T) {
	_, encode := cr193Cases(t)
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
			if encErr != nil {
				t.Fatalf("Encode: %v", encErr)
			}
			if got := hex.EncodeToString(out); got != c.Payload {
				t.Errorf("Encode = %s, want %s", got, c.Payload)
			}
		})
	}
}
