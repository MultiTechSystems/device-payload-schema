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

// CR-2026-176 (PS-361 amended, PS-590 to PS-592): template string fields. The cases are
// shared with the reference, the generated codec, Java and C# in
// tests/cr-2026-176-cases.yaml.

type cr176Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Payload  string `yaml:"payload"`
	Input    string `yaml:"input"`
	Expected any    `yaml:"expected"`
	Warning  string `yaml:"warning"`
	Error    string `yaml:"error"`
}

func cr176Cases(t *testing.T) (decode, encode, load []cr176Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-176-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr176Case `yaml:"decode"`
		Cases  []cr176Case `yaml:"cases"`
		Load   []cr176Case `yaml:"load"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Decode) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases, doc.Load
}

// cr176JSON normalises a value through JSON, so numbers of any Go type compare equal.
func cr176JSON(t *testing.T, v any) any {
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal %v: %v", v, err)
	}
	var out any
	_ = json.Unmarshal(raw, &out)
	return out
}

func TestCR176DecodeCases(t *testing.T) {
	decode, _, _ := cr176Cases(t)
	for _, c := range decode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			payload, _ := hex.DecodeString(c.Payload)
			out, decErr := s.Decode(payload)
			if decErr != nil {
				t.Fatalf("Decode: %v", decErr)
			}
			warnings, _ := out["_warnings"].([]string)
			delete(out, "_warnings")
			delete(out, "_quality")
			if got, want := cr176JSON(t, out), cr176JSON(t, c.Expected); !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %v, want %v", got, want)
			}
			found := false
			for _, w := range warnings {
				if c.Warning != "" && strings.Contains(w, c.Warning) {
					found = true
				}
				if c.Warning == "" && strings.Contains(w, "PS-591") {
					t.Errorf("unexpected warning %q", w)
				}
			}
			if c.Warning != "" && !found {
				t.Errorf("warnings %v, want %q", warnings, c.Warning)
			}
		})
	}
}

func TestCR176EncodeCases(t *testing.T) {
	_, encode, _ := cr176Cases(t)
	for _, c := range encode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			var data map[string]any
			if err := json.Unmarshal([]byte(c.Input), &data); err != nil {
				t.Fatalf("input: %v", err)
			}
			for _, ordered := range []bool{false, true} {
				var out []byte
				var encErr error
				if ordered {
					out, encErr = s.EncodeOrdered(data, nil)
				} else {
					out, encErr = s.Encode(data)
				}
				if encErr != nil {
					t.Fatalf("Encode (ordered %v): %v", ordered, encErr)
				}
				if got := hex.EncodeToString(out); got != c.Payload {
					t.Errorf("Encode (ordered %v) = %s, want %s", ordered, got, c.Payload)
				}
			}
		})
	}
}

func TestCR176LoadCases(t *testing.T) {
	_, _, load := cr176Cases(t)
	for _, c := range load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}
