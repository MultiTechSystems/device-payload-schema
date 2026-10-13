package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-156 (0.5.5, PS-567 to PS-569): `sampled` on a repeat. A good declaration is
// carried by `_meta` with its defaults filled in and changes no decoded value; a bad one is
// rejected at load with the spec prototype's text. The cases are shared with the reference,
// Java and C# in tests/cr-2026-156-cases.yaml.

type cr156Case struct {
	Name    string `yaml:"name"`
	Schema  string `yaml:"schema"`
	Payload string `yaml:"payload"`
	Sampled string `yaml:"sampled"`
	Decoded string `yaml:"decoded"`
	Error   string `yaml:"error"`
}

func TestCR156SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-156-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr156Case `yaml:"cases"`
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
			got := out["_meta"].(map[string]any)["fields"].(map[string]any)["pm25"].(map[string]any)["sampled"]
			var want any
			if err := json.Unmarshal([]byte(c.Sampled), &want); err != nil {
				t.Fatal(err)
			}
			if asJSON(t, got) != asJSON(t, want) {
				t.Errorf("sampled = %s\nwant %s", asJSON(t, got), asJSON(t, want))
			}
			if c.Decoded != "" {
				var wantDecoded any
				_ = json.Unmarshal([]byte(c.Decoded), &wantDecoded)
				// Decoding is unchanged: the elements are reported as they were.
				if asJSON(t, out["pm25"]) != asJSON(t, wantDecoded) {
					t.Errorf("pm25 = %s, want %s", asJSON(t, out["pm25"]), c.Decoded)
				}
			}
		})
	}
}
