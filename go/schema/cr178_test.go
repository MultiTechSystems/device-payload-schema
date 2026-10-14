package schema

import (
	"encoding/hex"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-178 (0.5.7, PS-178, PS-596): `_meta` entries carry `display_unit`, the field's
// `unit` as declared, beside `unit`. The cases are shared with the reference, Java and C# in
// tests/cr-2026-178-cases.yaml.

func TestCR178SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-178-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []struct {
			Name        string `yaml:"name"`
			Schema      string `yaml:"schema"`
			Payload     string `yaml:"payload"`
			Path        string `yaml:"path"`
			Unit        string `yaml:"unit"`
			DisplayUnit string `yaml:"display_unit"`
		} `yaml:"cases"`
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
			payload, _ := hex.DecodeString(c.Payload)
			out, err := s.Interpret(payload, InputContext{})
			if err != nil {
				t.Fatalf("Interpret: %v", err)
			}
			entry := out["_meta"].(map[string]any)["fields"].(map[string]any)
			for _, key := range strings.Split(c.Path, ".") {
				entry = entry[key].(map[string]any)
			}
			for key, want := range map[string]string{"unit": c.Unit, "display_unit": c.DisplayUnit} {
				got, present := entry[key]
				if want == "absent" {
					if present {
						t.Errorf("%s = %v, want absent", key, got)
					}
				} else if got != want {
					t.Errorf("%s = %v, want %s", key, got, want)
				}
			}
		})
	}
}
