package schema

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// CR-2026-169 (0.5.7, PS-581): which port entry an encoder uses. A caller's FPort chooses
// its entry (or `default`), an uplink entry is refused only for a downlink; without an
// FPort the single declared downlink entry, else the single downlink-capable one, else a
// rejection. The cases are shared with the reference, the generated codec, Java and C# in
// tests/cr-2026-169-cases.yaml. A zero fPort is none given: ports are 1 to 255.

type cr169Case struct {
	Name      string `yaml:"name"`
	Schema    string `yaml:"schema"`
	Input     string `yaml:"input"`
	FPort     int    `yaml:"fport"`
	Direction string `yaml:"direction"`
	Payload   string `yaml:"payload"`
	Used      int    `yaml:"used"`
	Error     string `yaml:"error"`
}

func TestCR169SharedCases(t *testing.T) {
	raw, err := os.ReadFile(repoPath(t, "tests", "cr-2026-169-cases.yaml"))
	if err != nil {
		t.Fatalf("read cases: %v", err)
	}
	var doc struct {
		Cases []cr169Case `yaml:"cases"`
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
			direction := DirectionDownlink
			if c.Direction == "none" {
				direction = ""
			}
			out, encErr := s.EncodeToResultDirection(data, c.FPort, direction)
			if c.Error != "" {
				if encErr == nil || !strings.Contains(encErr.Error(), c.Error) {
					t.Errorf("encode = %v, %v; want error %q", out, encErr, c.Error)
				}
				return
			}
			if encErr != nil {
				t.Fatalf("encode: %v", encErr)
			}
			if got := hex.EncodeToString(out.Payload); got != c.Payload {
				t.Errorf("payload = %s, want %s", got, c.Payload)
			}
			if out.FPort != c.Used {
				t.Errorf("FPort = %d, want %d", out.FPort, c.Used)
			}
		})
	}
}

// PS-459 is a decoder's rule: an encoder given no FPort chooses an entry.
func TestCR169EncodeWithoutAPortChoosesTheDownlinkEntry(t *testing.T) {
	s, err := ParseSchema("name: t\nports:\n  2: {direction: downlink, fields: [{name: a, type: u8}]}\n")
	if err != nil {
		t.Fatal(err)
	}
	out, err := s.Encode(map[string]any{"a": 3})
	if err != nil || hex.EncodeToString(out) != "03" {
		t.Errorf("Encode = %x, %v; want 03", out, err)
	}
}
