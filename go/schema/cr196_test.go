package schema

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-196 (PS-635 to PS-637): a `type: number` field declaring `source` reports the
// FPort, the payload length or the bytes remaining, on decode, encode and load. The cases
// are shared with the reference, the generated codec, Java and C# in
// tests/cr-2026-196-cases.yaml.

type cr196Case struct {
	Name     string    `yaml:"name"`
	Schema   string    `yaml:"schema"`
	Payload  string    `yaml:"payload"`
	FPort    *int      `yaml:"fport"`
	Input    string    `yaml:"input"`
	Expected any       `yaml:"expected"`
	Error    string    `yaml:"error"`
	Warnings *[]string `yaml:"warnings"`
}

func cr196Cases(t *testing.T) (decode, encode, load []cr196Case) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-196-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Decode []cr196Case `yaml:"decode"`
		Cases  []cr196Case `yaml:"cases"`
		Load   []cr196Case `yaml:"load"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Decode, doc.Cases, doc.Load
}

func TestCR196DecodeCases(t *testing.T) {
	decode, _, _ := cr196Cases(t)
	for _, c := range decode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			payload, _ := hex.DecodeString(c.Payload)
			var out map[string]any
			if c.FPort != nil {
				out, err = s.DecodeWithPort(payload, *c.FPort)
			} else {
				out, err = s.Decode(payload)
			}
			if err != nil {
				t.Fatalf("Decode: %v", err)
			}
			warnings, _ := out["_warnings"].([]string)
			delete(out, "_warnings")
			if c.Warnings != nil && fmt.Sprintf("%q", warnings) != fmt.Sprintf("%q", *c.Warnings) {
				t.Errorf("warnings %q, want %q", warnings, *c.Warnings)
			}
			want, _ := c.Expected.(map[string]any)
			if len(out) != len(want) {
				t.Errorf("Decode = %v, want %v", out, want)
			}
			for k, v := range want {
				if fmt.Sprint(out[k]) != fmt.Sprint(v) {
					t.Errorf("Decode[%s] = %q, want %q", k, fmt.Sprint(out[k]), fmt.Sprint(v))
				}
			}
		})
	}
}

func TestCR196EncodeCases(t *testing.T) {
	_, encode, _ := cr196Cases(t)
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
			port := 0
			if c.FPort != nil {
				port = *c.FPort
			}
			r, encErr := s.EncodeToResultDirection(data, port, "")
			if c.Error != "" {
				if encErr == nil || !strings.Contains(encErr.Error(), c.Error) {
					t.Errorf("Encode = %v, %v; want error %q", r, encErr, c.Error)
				}
				return
			}
			if encErr != nil {
				t.Fatalf("Encode: %v", encErr)
			}
			if got := hex.EncodeToString(r.Payload); got != c.Payload {
				t.Errorf("Encode = %s, want %s", got, c.Payload)
			}
		})
	}
}

func TestCR196LoadCases(t *testing.T) {
	_, _, load := cr196Cases(t)
	for _, c := range load {
		t.Run(c.Name, func(t *testing.T) {
			_, err := ParseSchema(c.Schema)
			if err == nil || !strings.Contains(err.Error(), c.Error) {
				t.Errorf("ParseSchema = %v; want error %q", err, c.Error)
			}
		})
	}
}

// TestCR196OrderedPaths: the ordered API checks a given FPort too.
func TestCR196OrderedPaths(t *testing.T) {
	s, err := ParseSchema("name: t\nfields:\n  - {name: x, type: u8}\n  - {name: port, type: number, source: $fPort}\n")
	if err != nil {
		t.Fatal(err)
	}
	out, order, err := s.DecodeOrderedWithPort([]byte{5}, 3)
	if err != nil || fmt.Sprint(out["port"]) != "3" {
		t.Fatalf("DecodeOrderedWithPort = %v, %v", out, err)
	}
	if b, err := s.EncodeOrderedWithPort(out, 3, order); err != nil || hex.EncodeToString(b) != "05" {
		t.Errorf("EncodeOrderedWithPort = %x, %v", b, err)
	}
	if _, err := s.EncodeOrdered(out, order); err == nil || !strings.Contains(err.Error(), "gives no FPort (PS-637)") {
		t.Errorf("EncodeOrdered with no FPort = %v", err)
	}
}
