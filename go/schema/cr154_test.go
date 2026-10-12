package schema

import (
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-154 (PS-566): a negative zero is reported as 0 and written as the positive-zero
// word, for every floating-point type. The cases are shared with the reference, the generated
// codec, Java and C# in tests/cr-2026-154-cases.yaml.

type cr154Case struct {
	Name     string `yaml:"name"`
	Schema   string `yaml:"schema"`
	Input    string `yaml:"input"`
	Payload  string `yaml:"payload"`
	Reencode string `yaml:"reencode"`
}

func loadCR154(t *testing.T) (encode, decode []cr154Case) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "tests", "cr-2026-154-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Encode []cr154Case `yaml:"encode_cases"`
		Decode []cr154Case `yaml:"decode_cases"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil || len(doc.Encode) == 0 || len(doc.Decode) == 0 {
		t.Fatalf("parse cases: %v", err)
	}
	return doc.Encode, doc.Decode
}

func TestCR154NegativeZeroEncode(t *testing.T) {
	encode, _ := loadCR154(t)
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
			out, err := s.Encode(data)
			if err != nil {
				t.Fatalf("Encode: %v", err)
			}
			if got := hex.EncodeToString(out); got != c.Payload {
				t.Errorf("Encode = %s, want %s", got, c.Payload)
			}
		})
	}
}

func TestCR154NegativeZeroDecodeAndReencode(t *testing.T) {
	_, decode := loadCR154(t)
	for _, c := range decode {
		t.Run(c.Name, func(t *testing.T) {
			s, err := ParseSchema(c.Schema)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			raw, _ := hex.DecodeString(c.Payload)
			res, err := s.Decode(raw)
			if err != nil {
				t.Fatalf("Decode: %v", err)
			}
			x, ok := toFloat64(res["x"])
			if !ok || x != 0 || math.Signbit(x) {
				t.Errorf("Decode reported %v (%T), want a positive 0", res["x"], res["x"])
			}
			out, err := s.Encode(res)
			if err != nil {
				t.Fatalf("Encode: %v", err)
			}
			if got := hex.EncodeToString(out); got != c.Reencode {
				t.Errorf("Encode(decoded) = %s, want %s", got, c.Reencode)
			}
		})
	}
}
