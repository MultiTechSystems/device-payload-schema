package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-162 (PS-572 to PS-574): Interpret with DecodeRecord adds `_decode`; an encoder
// given it follows it and rejects input contradicting it; where neither the input nor the
// record gives a match's case, this encoder derives it. The cases are shared with the
// reference, the generated codec, Java and C# in tests/cr-2026-162-cases.yaml.

type cr162Step struct {
	Input   string  `yaml:"input"`
	Record  string  `yaml:"record"`
	Payload *string `yaml:"payload"`
	Error   string  `yaml:"error"`
}

type cr162Case struct {
	Name   string `yaml:"name"`
	Schema string `yaml:"schema"`
	FPort  *int   `yaml:"fport"`
	Decode *struct {
		Payload   string `yaml:"payload"`
		Record    string `yaml:"record"`
		Matches   string `yaml:"matches"`
		RoundTrip bool   `yaml:"round_trip"`
	} `yaml:"decode"`
	Encode []cr162Step `yaml:"encode"`
}

// cr162Same is JSON equality, numbers by value, a boolean only a boolean.
func cr162Same(want, got any) bool {
	wb, _ := want.(bool)
	gb, _ := got.(bool)
	_, wIsBool := want.(bool)
	_, gIsBool := got.(bool)
	if wIsBool || gIsBool {
		return wIsBool && gIsBool && wb == gb
	}
	if w, ok := toFloat64(want); ok {
		if g, ok := toFloat64(got); ok {
			return w == g
		}
	}
	if wm := asStringMap(want); wm != nil {
		gm := asStringMap(got)
		if gm == nil || len(gm) != len(wm) {
			return false
		}
		for k, v := range wm {
			gv, ok := gm[k]
			if !ok || !cr162Same(v, gv) {
				return false
			}
		}
		return true
	}
	return want == got
}

func TestCR162SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-162-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr162Case `yaml:"cases"`
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
			port := 0
			if c.FPort != nil {
				port = *c.FPort
			}
			var decoded map[string]any
			if c.Decode != nil {
				payload, _ := hex.DecodeString(c.Decode.Payload)
				decoded, err = s.Interpret(payload, InputContext{FPort: c.FPort, DecodeRecord: true})
				if err != nil {
					t.Fatalf("interpret: %v", err)
				}
				record := asStringMap(decoded["_decode"])
				if c.Decode.Record != "" {
					var want any
					if err := json.Unmarshal([]byte(c.Decode.Record), &want); err != nil {
						t.Fatalf("record: %v", err)
					}
					// Through JSON, as a report carries it.
					text, _ := json.Marshal(record)
					var got any
					_ = json.Unmarshal(text, &got)
					if !cr162Same(want, got) {
						t.Errorf("_decode = %s, want %s", text, c.Decode.Record)
					}
				}
				if c.Decode.Matches != "" {
					var want []string
					_ = json.Unmarshal([]byte(c.Decode.Matches), &want)
					got := asStringMap(record["matches"])
					if len(got) != len(want) {
						t.Errorf("matches %v, want %v", got, want)
					}
					for _, w := range want {
						if _, ok := got[w]; !ok {
							t.Errorf("matches lack %s: %v", w, got)
						}
					}
				}
				var plain map[string]any
				if c.FPort != nil {
					plain, err = s.DecodeWithPort(payload, port)
				} else {
					plain, err = s.Decode(payload)
				}
				if err != nil {
					t.Fatalf("decode: %v", err)
				}
				if _, ok := plain["_decode"]; ok {
					t.Errorf("Decode carries _decode")
				}
				if c.Decode.RoundTrip {
					out, err := s.EncodeWithRecord(decoded, port, nil)
					if err != nil {
						t.Fatalf("round trip: %v", err)
					}
					if got := hex.EncodeToString(out); got != c.Decode.Payload {
						t.Errorf("round trip = %s, want %s", got, c.Decode.Payload)
					}
				}
			}
			for _, step := range c.Encode {
				var data map[string]any
				if err := json.Unmarshal([]byte(step.Input), &data); err != nil {
					t.Fatalf("input: %v", err)
				}
				var record map[string]any
				switch step.Record {
				case "":
				case "decoded":
					record = asStringMap(decoded["_decode"])
				default:
					if err := json.Unmarshal([]byte(step.Record), &record); err != nil {
						t.Fatalf("record: %v", err)
					}
				}
				out, encErr := s.EncodeWithRecord(data, port, record)
				if step.Error != "" {
					if encErr == nil || !strings.Contains(encErr.Error(), step.Error) {
						t.Errorf("%s: = %x, %v; want error %q", step.Input, out, encErr, step.Error)
					}
					continue
				}
				if encErr != nil {
					t.Errorf("%s: %v", step.Input, encErr)
					continue
				}
				if got := hex.EncodeToString(out); step.Payload == nil || got != *step.Payload {
					t.Errorf("%s: = %s, want %v", step.Input, got, step.Payload)
				}
			}
		})
	}
}
