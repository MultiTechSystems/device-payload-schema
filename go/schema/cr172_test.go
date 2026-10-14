package schema

import (
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"sort"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-172 (PS-119, PS-584): compute `pow`; a result that is not a finite real number omits
// the field. The cases are shared with the reference, the generated codec, Java and C# in
// tests/cr-2026-172-cases.yaml.

type cr172Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Payload  string `yaml:"payload"`
	Expected string `yaml:"expected"`
}

func TestCR172ComputePow(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-172-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr172Case `yaml:"decode_cases"`
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
			got, err := s.Decode(payload)
			if err != nil {
				t.Fatalf("Decode: %v", err)
			}
			var want map[string]float64
			if err := json.Unmarshal([]byte(c.Expected), &want); err != nil {
				t.Fatalf("expected: %v", err)
			}
			var gotKeys, wantKeys []string
			for k := range got {
				gotKeys = append(gotKeys, k)
			}
			for k := range want {
				wantKeys = append(wantKeys, k)
			}
			sort.Strings(gotKeys)
			sort.Strings(wantKeys)
			if len(gotKeys) != len(wantKeys) {
				t.Fatalf("keys %v, want %v", gotKeys, wantKeys)
			}
			for i := range gotKeys {
				if gotKeys[i] != wantKeys[i] {
					t.Fatalf("keys %v, want %v", gotKeys, wantKeys)
				}
			}
			for k, w := range want {
				g, ok := toFloat64(got[k])
				if !ok || math.Abs(g-w) > 1e-9*math.Max(1, math.Abs(w)) {
					t.Errorf("%s = %v, want %v", k, got[k], w)
				}
			}
		})
	}
}
