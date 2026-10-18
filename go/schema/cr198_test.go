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

// CR-2026-198 (PS-621 to PS-623 amended, PS-639, PS-640): packed records of several
// fields, a bit offset of any size or by reference, and a packed repeat that reads in
// place. The cases are shared with the reference, the generated codec, Java and C# in
// tests/cr-2026-198-cases.yaml.

type cr198Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Payload  string `yaml:"payload"`
	Input    string `yaml:"input"`
	Expected any    `yaml:"expected"`
	Error    string `yaml:"error"`
}

func cr198Cases(t *testing.T) (decode, encode, load []cr198Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-198-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr198Case `yaml:"decode"`
		Cases  []cr198Case `yaml:"cases"`
		Load   []cr198Case `yaml:"load"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases, doc.Load
}

func TestCR198DecodeCases(t *testing.T) {
	decode, _, _ := cr198Cases(t)
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
			var want, got any
			if err := json.Unmarshal([]byte(c.Expected.(string)), &want); err != nil {
				t.Fatalf("expected: %v", err)
			}
			encoded, _ := json.Marshal(out)
			_ = json.Unmarshal(encoded, &got)
			if !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %s, want %s", encoded, c.Expected)
			}
		})
	}
}

func TestCR198EncodeCases(t *testing.T) {
	_, encode, _ := cr198Cases(t)
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

func TestCR198LoadCases(t *testing.T) {
	_, _, load := cr198Cases(t)
	for _, c := range load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}
