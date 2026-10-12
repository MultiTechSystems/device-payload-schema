package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-141 (PS-551): a bit range or bool that does not consume, absent from the input,
// is derived from the fields that write its bits; a supplied one must agree with them.
// The cases are shared with the reference, the generated codec, Java and C# in
// tests/cr-2026-141-cases.yaml.

type cr141Case struct {
	Name    string `yaml:"name"`
	Schema  string `yaml:"schema"`
	Input   string `yaml:"input"`
	Payload string `yaml:"payload"`
	Error   string `yaml:"error"`
	Decoded string `yaml:"decoded"`
}

func TestCR141SharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "tests", "cr-2026-141-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr141Case `yaml:"cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse cases: %v", err)
	}
	if len(doc.Cases) == 0 {
		t.Fatal("no cases")
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
					t.Errorf("Encode error = %v, want %q", encErr, c.Error)
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
			if c.Decoded == "" {
				return
			}
			decoded, err := s.Decode(out)
			if err != nil {
				t.Fatalf("Decode: %v", err)
			}
			text, _ := json.Marshal(decoded)
			var got, want any
			_ = json.Unmarshal(text, &got)
			_ = json.Unmarshal([]byte(c.Decoded), &want)
			if !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %s, want %s", text, c.Decoded)
			}
		})
	}
}
