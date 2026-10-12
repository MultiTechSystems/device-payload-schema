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

// CR-2026-153 (PS-563, PS-564, PS-565): ties go to the case declared first, the bytes a
// `remaining` match writes select the case it chose, and a match choosing no case fails
// unless its default is `skip`. The cases are shared with the reference, the generated
// codec, Java and C# in tests/cr-2026-153-cases.yaml.

func TestCR153SharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "tests", "cr-2026-153-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []silentEmptyCase `yaml:"cases"`
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
			for _, encode := range []struct {
				api string
				run func() ([]byte, error)
			}{
				{"Encode", func() ([]byte, error) { return s.Encode(data) }},
				{"EncodeOrdered", func() ([]byte, error) { return s.EncodeOrdered(data, nil) }},
			} {
				out, encErr := encode.run()
				if c.Error != "" {
					if encErr == nil || !strings.Contains(encErr.Error(), c.Error) {
						t.Errorf("%s = %x, %v; want error %q", encode.api, out, encErr, c.Error)
					}
					if len(out) != 0 {
						t.Errorf("%s returned payload %x with its error", encode.api, out)
					}
					continue
				}
				if encErr != nil {
					t.Fatalf("%s: %v", encode.api, encErr)
				}
				if c.Payload == nil {
					t.Fatalf("case gives neither payload nor error")
				}
				if got := hex.EncodeToString(out); got != *c.Payload {
					t.Errorf("%s = %s, want %s", encode.api, got, *c.Payload)
				}
			}
		})
	}
}

// The empty-payload guard names the first name in declaration order, where it checked
// the input's keys sorted: 'z' is declared before 'a'.
func TestEmptyPayloadGuardDeclarationOrder(t *testing.T) {
	s, err := ParseSchema("name: t\nfields:\n  - {name: z, type: u8}\n  - {name: a, type: u8}\n")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	err = emptyPayloadError(s.Fields, map[string]any{"a": 1.0, "z": 2.0}, s.keyOrder)
	if err == nil || !strings.Contains(err.Error(), "'z'") {
		t.Fatalf("emptyPayloadError = %v, want it to name 'z'", err)
	}
}
