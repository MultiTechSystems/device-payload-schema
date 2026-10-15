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

// CR-2026-185 (PS-585 amended, PS-608): a checksum field's placement, checked at load, and
// an empty covered range, an error on decode and encode. The cases are shared with the
// reference, the generated codec, Java and C# in tests/cr-2026-185-cases.yaml; its
// `normalize` list (PS-609) is the reference's and the generated codec's alone.

type cr185Case struct {
	Name     string         `yaml:"name"`
	Schema   string         `yaml:"schema"`
	Payload  string         `yaml:"payload"`
	Input    string         `yaml:"input"`
	Expected map[string]any `yaml:"expected"`
	Warning  string         `yaml:"warning"`
	Error    string         `yaml:"error"`
}

func cr185Cases(t *testing.T) (decode, encode, load []cr185Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-185-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr185Case `yaml:"decode"`
		Cases  []cr185Case `yaml:"cases"`
		Load   []cr185Case `yaml:"load"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Decode) == 0 ||
		len(doc.Cases) == 0 || len(doc.Load) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases, doc.Load
}

func TestCR185DecodeCases(t *testing.T) {
	decode, _, _ := cr185Cases(t)
	for _, c := range decode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			payload, _ := hex.DecodeString(c.Payload)
			out, decErr := s.Decode(payload)
			if c.Error != "" {
				if decErr == nil || !strings.Contains(decErr.Error(), c.Error) {
					t.Errorf("Decode = %v, %v; want error %q", out, decErr, c.Error)
				}
				return
			}
			if decErr != nil {
				t.Fatalf("Decode: %v", decErr)
			}
			warnings, _ := out["_warnings"].([]string)
			delete(out, "_warnings")
			if c.Warning != "" {
				found := false
				for _, w := range warnings {
					found = found || strings.Contains(w, c.Warning)
				}
				if !found {
					t.Errorf("warnings %q do not contain %q", warnings, c.Warning)
				}
			}
			got, want := cr174Normalize(out), cr174Normalize(c.Expected)
			if !reflect.DeepEqual(got, want) {
				t.Errorf("Decode = %v, want %v", got, want)
			}
		})
	}
}

func TestCR185EncodeCases(t *testing.T) {
	_, encode, _ := cr185Cases(t)
	for _, c := range encode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			// Both encode APIs give the same bytes.
			for _, api := range []string{"plain", "ordered"} {
				var data map[string]any
				if err := json.Unmarshal([]byte(c.Input), &data); err != nil {
					t.Fatalf("input: %v", err)
				}
				var out []byte
				var encErr error
				if api == "plain" {
					out, encErr = s.Encode(data)
				} else {
					out, encErr = s.EncodeOrdered(data, nil)
				}
				if c.Error != "" {
					if encErr == nil || encErr.Error() != c.Error {
						t.Errorf("%s: Encode = %x, %v; want error %q", api, out, encErr, c.Error)
					}
					if len(out) != 0 {
						t.Errorf("%s: Encode returned payload %x with its error", api, out)
					}
					continue
				}
				if encErr != nil {
					t.Fatalf("%s: Encode: %v", api, encErr)
				}
				if got := hex.EncodeToString(out); got != c.Payload {
					t.Errorf("%s: Encode = %s, want %s", api, got, c.Payload)
				}
			}
		})
	}
}

func TestCR185LoadCases(t *testing.T) {
	_, _, load := cr185Cases(t)
	for _, c := range load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}
