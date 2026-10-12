package schema

import (
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-149 (PS-558, PS-559): bf16 is the upper half of a binary32; an encoder rounds
// once from binary64, a tie to even, and rejects a finite overflow. The cases are shared
// with the reference, the generated codec, Java and C# in tests/cr-2026-149-cases.yaml.

type cr149Case struct {
	Name    string `yaml:"name"`
	Schema  string `yaml:"schema"`
	Input   string `yaml:"input"`
	Payload string `yaml:"payload"`
	Error   string `yaml:"error"`
}

func TestCR149SharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "tests", "cr-2026-149-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr149Case `yaml:"cases"`
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

// NaN and the infinities cannot be written in a JSON input, so the shared cases omit them:
// they encode as their PS-558 words, and decode as absent (PS-282).
func TestCR149SpecialValues(t *testing.T) {
	s, err := ParseSchema("name: t\nfields:\n  - {name: x, type: bf16}\n")
	if err != nil {
		t.Fatal(err)
	}
	for want, v := range map[string]float64{
		"7f80": math.Inf(1), "ff80": math.Inf(-1), "7fc0": math.NaN(),
	} {
		out, err := s.Encode(map[string]any{"x": v})
		if err != nil || hex.EncodeToString(out) != want {
			t.Errorf("Encode(%v) = %x, %v; want %s", v, out, err, want)
		}
	}
	for _, word := range []string{"7f80", "ff80", "7fc1"} {
		raw, _ := hex.DecodeString(word)
		res, err := s.Decode(raw)
		if err != nil {
			t.Fatal(err)
		}
		if _, present := res["x"]; present {
			t.Errorf("Decode(%s) reported %v, want it absent", word, res["x"])
		}
	}
}

// A document-level endian applies on encode to every field with none of its own, through
// every public entry point (the spec prototype's encode() ignored it).
func TestCR149DocumentEndianOnEveryEncodeEntryPoint(t *testing.T) {
	s, err := ParseSchema("name: t\nendian: little\nports:\n  1:\n    fields:\n      - {name: a, type: u16}\n      - {name: b, type: f16}\n")
	if err != nil {
		t.Fatal(err)
	}
	data := map[string]any{"a": 1, "b": 1.0}
	order := []string{"a", "b"}
	want := "0100003c"
	check := func(name string, out []byte, err error) {
		if err != nil || hex.EncodeToString(out) != want {
			t.Errorf("%s = %x, %v; want %s", name, out, err, want)
		}
	}
	out, err := s.EncodeWithPort(data, 1)
	check("EncodeWithPort", out, err)
	out, err = s.EncodeOrderedWithPort(data, 1, order)
	check("EncodeOrderedWithPort", out, err)
	plain, err := ParseSchema("name: t\nendian: little\nfields:\n  - {name: a, type: u16}\n  - {name: b, type: f16}\n")
	if err != nil {
		t.Fatal(err)
	}
	out, err = plain.Encode(data)
	check("Encode", out, err)
	out, err = plain.EncodeOrdered(data, order)
	check("EncodeOrdered", out, err)
}
