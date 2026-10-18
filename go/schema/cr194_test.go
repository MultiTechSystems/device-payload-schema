package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-194 (PS-436, PS-437, PS-438, PS-440, PS-270): a one-field tlv tag's "[*]" and
// "[!n]" keys rank below exact keys, whatever the declaration order, and a tlv has no
// `default` case. The cases are shared with the reference, Java and C# in
// tests/cr-2026-194-cases.yaml.

func TestCR194SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-194-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []struct {
			Name    string `yaml:"name"`
			Schema  string `yaml:"schema"`
			Error   string `yaml:"error"`
			Decodes []struct {
				Payload string `yaml:"payload"`
				Decoded string `yaml:"decoded"`
			} `yaml:"decodes"`
		} `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	for _, c := range doc.Cases {
		t.Run(c.Name, func(t *testing.T) {
			// Parsed several times: the ranking must not depend on Go's map order.
			for round := 0; round < 20; round++ {
				s, err := ParseSchema(c.Schema)
				if c.Error != "" {
					if err == nil || !strings.Contains(err.Error(), c.Error) {
						t.Fatalf("ParseSchema error = %v; want %q", err, c.Error)
					}
					continue
				}
				if err != nil {
					t.Fatalf("parse: %v", err)
				}
				for _, d := range c.Decodes {
					payload, _ := hex.DecodeString(d.Payload)
					out, err := s.Decode(payload)
					if err != nil {
						t.Fatalf("Decode %s: %v", d.Payload, err)
					}
					var want any
					if err := json.Unmarshal([]byte(d.Decoded), &want); err != nil {
						t.Fatal(err)
					}
					if asJSON(t, out) != asJSON(t, want) {
						t.Errorf("decode %s = %s, want %s", d.Payload, asJSON(t, out), asJSON(t, want))
					}
				}
			}
		})
	}
}
