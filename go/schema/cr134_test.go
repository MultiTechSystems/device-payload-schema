package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-134 (PS-551): an encoder rejects input missing a required value, naming the
// field, and derives what the rest of the input fixes. The cases are shared with the
// reference, the generated codec, Java and C# in tests/cr-2026-134-cases.yaml, so all
// five are held to the same bytes and the same text.

type cr134Case struct {
	Name    string `yaml:"name"`
	Schema  string `yaml:"schema"`
	Input   string `yaml:"input"`
	Payload string `yaml:"payload"`
	Error   string `yaml:"error"`
}

func cr134Cases(t *testing.T) []cr134Case {
	t.Helper()
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-134-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr134Case `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse cases: %v", err)
	}
	if len(doc.Cases) == 0 {
		t.Fatal("no cases")
	}
	return doc.Cases
}

func TestCR134SharedCases(t *testing.T) {
	for _, c := range cr134Cases(t) {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			var data map[string]any
			if err := json.Unmarshal([]byte(c.Input), &data); err != nil {
				t.Fatalf("input: %v", err)
			}
			// Encode and EncodeToResult share one path; both are asserted, so neither
			// can return bytes the other refuses.
			out, encErr := s.Encode(data)
			result, resErr := s.EncodeToResult(data, 0)
			if c.Error != "" {
				if encErr == nil || !strings.Contains(encErr.Error(), c.Error) {
					t.Errorf("Encode error = %v, want %q", encErr, c.Error)
				}
				if len(out) != 0 {
					t.Errorf("Encode returned payload %x with its error", out)
				}
				if resErr == nil || !strings.Contains(resErr.Error(), c.Error) || result != nil {
					t.Errorf("EncodeToResult = %v, %v, want %q", result, resErr, c.Error)
				}
				return
			}
			if encErr != nil {
				t.Fatalf("Encode: %v", encErr)
			}
			if got := hex.EncodeToString(out); got != c.Payload {
				t.Errorf("Encode = %s, want %s", got, c.Payload)
			}
			if resErr != nil || len(result.Warnings) != 0 {
				t.Errorf("EncodeToResult = %v, %v; want no warnings", result, resErr)
			}
		})
	}
}
