package schema

import (
	"encoding/hex"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-179 (0.5.7, PS-470, PS-600): a `semantic` is a string, a TTN normalized path, and
// `_meta` carries it. The cases are shared with the reference, Java and C# in
// tests/cr-2026-179-cases.yaml; the `normalized` ones are the generated codec's and the
// reference's alone.

func TestCR179SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-179-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []struct {
			Name    string             `yaml:"name"`
			Schema  string             `yaml:"schema"`
			Payload string             `yaml:"payload"`
			Meta    map[string]*string `yaml:"meta"`
			Error   string             `yaml:"error"`
		} `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	ran := 0
	for _, c := range doc.Cases {
		if c.Error == "" && c.Meta == nil {
			continue
		}
		ran++
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
			fields := out["_meta"].(map[string]any)["fields"].(map[string]any)
			for name, want := range c.Meta {
				entry := fields[name].(map[string]any)
				got, has := entry["semantic"]
				if want == nil {
					if has {
						t.Errorf("%s: semantic = %v, want none", name, got)
					}
				} else if got != *want {
					t.Errorf("%s: semantic = %v, want %s", name, got, *want)
				}
			}
		})
	}
	if ran < 5 {
		t.Fatalf("ran %d cases", ran)
	}
}
