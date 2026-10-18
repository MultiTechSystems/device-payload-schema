package schema

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-199 (PS-641, PS-642): `trim: true` on an ascii field, on decode, encode
// and load. The cases are shared with the reference, the generated codec, Java and C# in
// tests/cr-2026-199-cases.yaml.

type cr199Case struct {
	Name     string    `yaml:"name"`
	Schema   string    `yaml:"schema"`
	Payload  string    `yaml:"payload"`
	Input    string    `yaml:"input"`
	Expected any       `yaml:"expected"`
	Error    string    `yaml:"error"`
	Warnings *[]string `yaml:"warnings"`
}

func cr199Cases(t *testing.T) (decode, encode, load []cr199Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-199-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr199Case `yaml:"decode"`
		Cases  []cr199Case `yaml:"cases"`
		Load   []cr199Case `yaml:"load"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases, doc.Load
}

func cr199SameWarnings(got, want []string) bool {
	return fmt.Sprintf("%q", got) == fmt.Sprintf("%q", want)
}

func TestCR199DecodeCases(t *testing.T) {
	decode, _, _ := cr199Cases(t)
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
			if c.Warnings != nil && !cr199SameWarnings(warnings, *c.Warnings) {
				t.Errorf("warnings %q, want %q", warnings, *c.Warnings)
			}
			want, _ := c.Expected.(map[string]any)
			if len(out) != len(want) {
				t.Errorf("Decode = %v, want %v", out, want)
			}
			for k, v := range want {
				if fmt.Sprint(out[k]) != fmt.Sprint(v) {
					t.Errorf("Decode[%s] = %q, want %q", k, fmt.Sprint(out[k]), fmt.Sprint(v))
				}
			}
		})
	}
}

func TestCR199EncodeCases(t *testing.T) {
	_, encode, _ := cr199Cases(t)
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
			r, encErr := s.EncodeToResult(data, 0)
			if c.Error != "" {
				if encErr == nil || !strings.Contains(encErr.Error(), c.Error) {
					t.Errorf("Encode = %v, %v; want error %q", r, encErr, c.Error)
				}
				if r != nil && len(r.Payload) != 0 {
					t.Errorf("Encode returned payload %x with its error", r.Payload)
				}
				return
			}
			if encErr != nil {
				t.Fatalf("Encode: %v", encErr)
			}
			if got := hex.EncodeToString(r.Payload); got != c.Payload {
				t.Errorf("Encode = %s, want %s", got, c.Payload)
			}
			if c.Warnings != nil && !cr199SameWarnings(r.Warnings, *c.Warnings) {
				t.Errorf("warnings %q, want %q", r.Warnings, *c.Warnings)
			}
		})
	}
}

func TestCR199LoadCases(t *testing.T) {
	_, _, load := cr199Cases(t)
	for _, c := range load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}
