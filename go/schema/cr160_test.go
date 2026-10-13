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

// CR-2026-160 (0.5.6, PS-059): u40/u48/u56 and s40/s48/s56 are bit-range bases only. A
// standalone `type: u56` is rejected at load; a bit range over one is read. The cases are
// shared with the reference, Java and C# in tests/cr-2026-160-cases.yaml.

func TestCR160SharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "tests", "cr-2026-160-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []struct {
			Name    string `yaml:"name"`
			Schema  string `yaml:"schema"`
			Payload string `yaml:"payload"`
			Decoded string `yaml:"decoded"`
			Error   string `yaml:"error"`
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
			out, err := s.Decode(payload)
			if err != nil {
				t.Fatalf("Decode: %v", err)
			}
			var want any
			if err := json.Unmarshal([]byte(c.Decoded), &want); err != nil {
				t.Fatal(err)
			}
			if asJSON(t, out) != asJSON(t, want) {
				t.Errorf("decoded = %s, want %s", asJSON(t, out), asJSON(t, want))
			}
		})
	}
}
