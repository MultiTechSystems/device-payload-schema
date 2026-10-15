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

// CR-2026-150 (PS-560, PS-561): an ascii field with `numeral` reports its digits as an
// integer, strictly, and writes them back canonically. The cases are shared with the
// reference, the generated codec, Java and C# in tests/cr-2026-173-cases.yaml.

type cr173Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Payload  string `yaml:"payload"`
	Input    string `yaml:"input"`
	Expected string `yaml:"expected"`
	Error    string `yaml:"error"`
}

func cr173Cases(t *testing.T) (decode, encode, load []cr173Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-173-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr173Case `yaml:"decode"`
		Cases  []cr173Case `yaml:"cases"`
		Load   []cr173Case `yaml:"load"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases, doc.Load
}

func TestCR173DecodeCases(t *testing.T) {
	decode, _, _ := cr173Cases(t)
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
			var got, want any
			text, _ := json.Marshal(out)
			_ = json.Unmarshal(text, &got)
			if err := json.Unmarshal([]byte(c.Expected), &want); err != nil {
				t.Fatalf("expected: %v", err)
			}
			if !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %s, want %s", text, c.Expected)
			}
		})
	}
}

func TestCR173EncodeCases(t *testing.T) {
	_, encode, _ := cr173Cases(t)
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

func TestCR173LoadCases(t *testing.T) {
	_, _, load := cr173Cases(t)
	for _, c := range load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}
