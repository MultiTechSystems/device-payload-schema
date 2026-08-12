package schema

// Encoding, measured against the decode corpus - the Go side of
// tests/test_encode_round_trip.py.
//
// Every corpus vector tests decoding. Nothing tested encoding here either, and this
// interpreter had the same gaps the Python one did before they were fixed: no TLV,
// `match`, `byte_group`, `repeat` or `$ref` on the encode path, a derived-field skip that
// only recognised the deprecated `formula` spelling, and no reversal of a `transform`
// chain.
//
// encode(decode(payload)) == payload cannot hold everywhere - a `skip` field's bytes are
// not recoverable from output that omits them, a rounding stage discards precision, and a
// `default` label stands for every value a table does not list (PS-269). So the floors
// are ratchets, not a target of 1191.

import (
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// encodeFloorTotal is the number of corpus vectors that re-encode to their exact
// payload. Raise it as encoding improves; never lower it without saying why.
//
// This test uses DecodeOrdered/EncodeOrdered, which carry the TLV channel sequence a Go
// map cannot hold. Encode alone still has to assume ascending tag order, which is how most
// devices here lay their channels out but not all - so the plain pair scores lower, and
// that is the reason the ordered pair exists.
const encodeFloorTotal = 1135

// encodeFloorByShape guards each layout separately, so a regression in one that works
// cannot hide behind the mass of one that does not. It has earned that: raising the total
// after adding the type switch's default case caught `flagged` dropping 9 vectors in the
// same run, which the total alone would have absorbed.
//
// Every shape now matches the Python, Java and C# figures except tlv, which is higher.
// That is not a better encoder: TLVOrder records the case key of each channel as it was
// read, so where two cases define the same field name under different tags - em500-smt
// carries `humidity` under both [4, 104] and [4, 202], with identical arithmetic - the tag
// that actually wrote those bytes goes back. The other four recover order from their output
// keys, which cannot tell those two cases apart, and pick the first.
var encodeFloorByShape = map[string]int{
	"tlv":         905,
	"flagged":     121,
	"plain fixed": 54,
	"match":       34,
	"byte_group":  17,
	"repeat":      4,
}

type encodeVector struct {
	Name       string         `yaml:"name"`
	Payload    string         `yaml:"payload"`
	FPort      *int           `yaml:"fPort"`
	FPortLower *int           `yaml:"fport"`
	Expected   map[string]any `yaml:"expected"`
}

type encodeSchemaFile struct {
	TestVectors []encodeVector `yaml:"test_vectors"`
}

// schemaShape names the construct that dominates a schema's layout, so a regression in a
// shape that works cannot hide behind the mass of one that does not.
func schemaShape(raw string) string {
	for _, key := range []string{"tlv:", "match:", "repeat", "flagged:", "byte_group:"} {
		if strings.Contains(raw, key) {
			return strings.TrimSuffix(key, ":")
		}
	}
	return "plain fixed"
}

func TestCorpusEncodeRoundTrip(t *testing.T) {
	root := filepath.Join("..", "..", "schemas", "devices")
	byShape := map[string]map[string]int{}
	total := 0

	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(path, ".yaml") {
			return nil
		}
		raw, readErr := os.ReadFile(path)
		if readErr != nil {
			return nil
		}
		var file encodeSchemaFile
		if yaml.Unmarshal(raw, &file) != nil {
			return nil
		}
		s, parseErr := ParseSchema(string(raw))
		if parseErr != nil {
			return nil
		}
		shape := schemaShape(string(raw))
		if byShape[shape] == nil {
			byShape[shape] = map[string]int{}
		}
		for _, v := range file.TestVectors {
			payload, hexErr := hex.DecodeString(strings.ReplaceAll(v.Payload, " ", ""))
			if hexErr != nil {
				continue
			}
			fport := v.FPort
			if fport == nil {
				fport = v.FPortLower
			}
			var decoded map[string]any
			var order []string
			var decErr error
			if fport != nil {
				decoded, order, decErr = s.DecodeOrderedWithPort(payload, *fport)
			} else {
				decoded, order, decErr = s.DecodeOrdered(payload)
			}
			if decErr != nil {
				continue
			}
			total++
			var out []byte
			var encErr error
			if fport != nil {
				out, encErr = s.EncodeOrderedWithPort(decoded, *fport, order)
			} else {
				out, encErr = s.EncodeOrdered(decoded, order)
			}
			switch {
			case encErr != nil:
				byShape[shape]["error"]++
			case string(out) == string(payload):
				byShape[shape]["round-trips"]++
			case len(out) != len(payload):
				byShape[shape]["length differs"]++
			default:
				byShape[shape]["bytes differ"]++
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk: %v", err)
	}

	exact := 0
	shapes := make([]string, 0, len(byShape))
	for shape := range byShape {
		shapes = append(shapes, shape)
		exact += byShape[shape]["round-trips"]
	}
	sort.Strings(shapes)
	for _, shape := range shapes {
		c := byShape[shape]
		t.Log(fmt.Sprintf("%-12s round-trips=%-5d length=%-4d bytes=%-4d error=%-4d",
			shape, c["round-trips"], c["length differs"], c["bytes differ"], c["error"]))
	}
	t.Log(fmt.Sprintf("total round-trips: %d of %d vectors decoded", exact, total))

	if exact < encodeFloorTotal {
		t.Errorf("only %d corpus vectors re-encode exactly, floor is %d", exact, encodeFloorTotal)
	}
	for shape, floor := range encodeFloorByShape {
		if got := byShape[shape]["round-trips"]; got < floor {
			t.Errorf("%s: %d re-encode exactly, floor is %d", shape, got, floor)
		}
	}
}
