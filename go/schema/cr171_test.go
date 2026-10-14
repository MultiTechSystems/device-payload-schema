package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-171 (PS-390): `ties: up` rounds an exact tie toward +infinity, as Math.round does,
// so a negative tie goes toward zero. The cases are shared with the reference, Java and C# in
// tests/cr-2026-171-cases.yaml.

func TestCR171SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-171-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr159Case `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	for _, c := range doc.Cases {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if c.Error != "" {
				if err == nil || !strings.Contains(err.Error(), c.Error) {
					t.Fatalf("ParseSchema error = %v, want one containing %q", err, c.Error)
				}
				return
			}
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			payload, _ := hex.DecodeString(strings.ReplaceAll(c.Payload, " ", ""))
			decoded, err := s.Decode(payload)
			if err != nil {
				t.Fatalf("decode: %v", err)
			}
			var expected map[string]any
			if err := json.Unmarshal([]byte(c.Expected), &expected); err != nil {
				t.Fatalf("expected: %v", err)
			}
			if !reflect.DeepEqual(cr159Normalize(decoded), cr159Normalize(expected)) {
				t.Fatalf("Decode = %v, want %v", decoded, expected)
			}
		})
	}
}
