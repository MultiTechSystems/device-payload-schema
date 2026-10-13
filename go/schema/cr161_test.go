package schema

import (
	"encoding/hex"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-161 (0.5.6, PS-570, PS-571): `_meta` entries carry `reports`, and a lookup's labels
// are of one type. The cases are shared with the reference, Java and C# in
// tests/cr-2026-161-cases.yaml.

func TestCR161SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-161-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []struct {
			Name           string `yaml:"name"`
			Schema         string `yaml:"schema"`
			Payload        string `yaml:"payload"`
			Reports        string `yaml:"reports"`
			ElementReports string `yaml:"element_reports"`
			Error          string `yaml:"error"`
		} `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	for _, c := range doc.Cases {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if c.Error != "" {
				if err == nil || !strings.Contains(err.Error(), c.Error) {
					t.Fatalf("ParseSchema error = %v; want %q", err, c.Error)
				}
				return
			}
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
			if c.ElementReports != "" {
				element := entry["element"].(map[string]any)
				if element["reports"] != c.ElementReports {
					t.Errorf("element reports = %v, want %s", element["reports"], c.ElementReports)
				}
			}
		})
	}
}
