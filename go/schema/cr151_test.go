package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-151 (PS-551 editorial, on spec main, not yet applied): a nested match that
// reads a discriminator fixes it where the input fills one single-value case of it that
// selects its branch. The cases are shared with the reference, the generated
// codec, Java and C# in tests/cr-2026-151-cases.yaml.

type cr151Case struct {
	Name    string `yaml:"name"`
	Schema  string `yaml:"schema"`
	Input   string `yaml:"input"`
	Payload string `yaml:"payload"`
	Error   string `yaml:"error"`
}

func TestCR151SharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "tests", "cr-2026-151-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr151Case `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	for _, c := range doc.Cases {
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
