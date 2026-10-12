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

// No silent empty encode (PS-442, PS-551): an encoder never returns success having
// written nothing for a construct whose decode reads bytes. The cases are shared with
// the reference, the generated codec, Java and C# in tests/silent-empty-cases.yaml.

type silentEmptyCase struct {
	Name    string  `yaml:"name"`
	Schema  string  `yaml:"schema"`
	Input   string  `yaml:"input"`
	Payload *string `yaml:"payload"`
	Error   string  `yaml:"error"`
}

func TestSilentEmptySharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "tests", "silent-empty-cases.yaml"))
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

// The guard no path reaches today - each construct now fails by name first - held
// directly, as tests/test_silent_empty_cases.py holds the reference's.
const guardSchema = `name: t
fields:
  - {name: battery, type: u8}
  - match:
      remaining: true
      cases:
        2:
          - {name: _w, type: u16}
          - {name: distance, type: number, ref: $_w}
      default:
        - {name: distance, type: string, value: none}
        - {name: kind, type: number, value: 4}
`

func TestEmptyPayloadGuard(t *testing.T) {
	s, err := ParseSchema(guardSchema)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	sources := declaredSources(s.Fields)
	if b := sources["battery"]; b == nil || !b.reads || len(b.literals) != 0 {
		t.Errorf("battery = %+v", b)
	}
	if d := sources["distance"]; d == nil || !d.reads || len(d.literals) != 1 || d.literals[0] != "none" {
		t.Errorf("distance = %+v", d)
	}
	if k := sources["kind"]; k == nil || k.reads || len(k.literals) != 1 || !sameLabel(k.literals[0], 4) {
		t.Errorf("kind = %+v", k)
	}
	want := "Encoding wrote no bytes, though the input gives 'battery', which the schema reads from the payload (PS-442)"
	if err := emptyPayloadError(s.Fields, map[string]any{"battery": 3.0}, s.keyOrder); err == nil || err.Error() != want {
		t.Errorf("battery: %v", err)
	}
	if err := emptyPayloadError(s.Fields, map[string]any{"distance": "none", "kind": 4.0}, s.keyOrder); err != nil {
		t.Errorf("constants: %v", err)
	}
	if err := emptyPayloadError(s.Fields, map[string]any{"distance": 12.0}, s.keyOrder); err == nil || !strings.Contains(err.Error(), "'distance'") {
		t.Errorf("distance 12: %v", err)
	}
	none := map[string]any{"battery": []any{}, "distance": "", "_w": 5.0, "other": 1.0, "x": nil}
	if err := emptyPayloadError(s.Fields, none, s.keyOrder); err != nil {
		t.Errorf("no bytes: %v", err)
	}
}
