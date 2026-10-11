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

// CR-2026-135 (PS-552, PS-168): an encoder rounds an integer field's value - a bit range's
// included - to the nearest integer, a tie to even, and rejects a value that rounds
// outside the field's type, a bit range's type being its width. The cases are shared with
// the reference, the generated codec, Java and C# in tests/cr-2026-135-cases.yaml.

type cr135Case struct {
	Name    string `yaml:"name"`
	Schema  string `yaml:"schema"`
	Input   string `yaml:"input"`
	Payload string `yaml:"payload"`
	Error   string `yaml:"error"`
}

func TestCR135SharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "tests", "cr-2026-135-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr135Case `yaml:"cases"`
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

// An integer input past 2^53 is not a float in disguise: a u64 given one is checked
// against its range and written exactly, as before.
func TestCR135U64TopOfRangeStillEncodes(t *testing.T) {
	s, err := ParseSchema("name: t\nfields:\n  - {name: x, type: u64}\n")
	if err != nil {
		t.Fatal(err)
	}
	out, err := s.Encode(map[string]any{"x": uint64(18446744073709551615)})
	if err != nil || hex.EncodeToString(out) != "ffffffffffffffff" {
		t.Errorf("Encode = %x, %v", out, err)
	}
}
