package schema

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-174 (PS-585 to PS-587): checksum fields, verified on decode and computed on
// encode. The cases are shared with the reference, the generated codec, Java and C# in
// tests/cr-2026-174-cases.yaml.

type cr174Case struct {
	Name     string         `yaml:"name"`
	Schema   string         `yaml:"schema"`
	Payload  string         `yaml:"payload"`
	Input    string         `yaml:"input"`
	Expected map[string]any `yaml:"expected"`
	Warning  string         `yaml:"warning"`
	Error    string         `yaml:"error"`
}

func cr174Cases(t *testing.T) (decode, encode, load []cr174Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-174-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr174Case `yaml:"decode"`
		Cases  []cr174Case `yaml:"cases"`
		Load   []cr174Case `yaml:"load"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Decode) == 0 ||
		len(doc.Cases) == 0 || len(doc.Load) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases, doc.Load
}

// cr174Normalize turns every number into a float64, so a decoded uint64 and an expected
// int compare numerically.
func cr174Normalize(v any) any {
	switch t := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, x := range t {
			out[k] = cr174Normalize(x)
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, x := range t {
			out[i] = cr174Normalize(x)
		}
		return out
	case int, int64, uint64, int32, uint32, uint8, uint16, int8, int16, float32, uint:
		var f float64
		fmt.Sscan(fmt.Sprint(t), &f)
		return f
	}
	return v
}

func TestCR174CheckValues(t *testing.T) {
	data := []byte("123456789")
	for algo, want := range map[string]uint64{
		"sum8": 0xDD, "xor8": 0x31, "crc8/smbus": 0xF4, "crc16/kermit": 0x2189,
	} {
		if got := computeChecksum(algo, data, 0); got != want {
			t.Errorf("%s over 123456789 = 0x%X, want 0x%X", algo, got, want)
		}
	}
}

func TestCR174DecodeCases(t *testing.T) {
	decode, _, _ := cr174Cases(t)
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

func TestCR174EncodeCases(t *testing.T) {
	_, encode, _ := cr174Cases(t)
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

func TestCR174LoadCases(t *testing.T) {
	_, _, load := cr174Cases(t)
	for _, c := range load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}
