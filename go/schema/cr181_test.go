package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-181 (PS-601, PS-602; PS-081, PS-527, PS-551 amended): a bytes, hex or base64
// value is written exactly as given. The cases are shared with the reference, the
// generated codec, Java and C# in tests/cr-2026-181-cases.yaml.

type cr181Case struct {
	Name    string `yaml:"name"`
	Schema  string `yaml:"schema"`
	Input   string `yaml:"input"`
	Payload string `yaml:"payload"`
	Error   string `yaml:"error"`
}

func TestCR181EncodeCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-181-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr181Case `yaml:"cases"`
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
