package schema

// Runs every test vector in the device corpus through this interpreter, the same
// vectors the Python, Java and C# suites read. Until this existed, the Go suite read
// two hardcoded schemas, which is how it came to return an empty result for every
// TLV schema in the repository without a test failing.
//
// Constructs this implementation does not yet support are expected to fail, so the
// pass count is compared against a committed floor rather than requiring the whole
// corpus. Raise the floor when a gap is closed; a drop means something regressed.

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// corpusFloor is the number of corpus vectors this interpreter is known to decode
// correctly. Raise it as gaps close.
// The remaining 15 are byte_group and computed-field constructs (oyster, ers) plus
// a missing `round` transform op. Named in the test output.
const corpusFloor = 1101

type corpusVector struct {
	Name     string         `yaml:"name"`
	Payload  string         `yaml:"payload"`
	FPort    *int           `yaml:"fPort"`
	Expected map[string]any `yaml:"expected"`
}

type corpusSchema struct {
	TestVectors []corpusVector `yaml:"test_vectors"`
}

func TestCorpusConformance(t *testing.T) {
	root := filepath.Join("..", "..", "schemas", "devices")
	var files []string
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() && strings.HasSuffix(path, ".yaml") {
			files = append(files, path)
		}
		return nil
	})
	if err != nil {
		t.Skipf("corpus unavailable: %v", err)
	}
	sort.Strings(files)

	var passed, failed, total int
	failures := map[string]int{}

	for _, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			t.Errorf("%s: %v", file, err)
			continue
		}
		var meta corpusSchema
		if err := yaml.Unmarshal(data, &meta); err != nil {
			continue
		}
		if len(meta.TestVectors) == 0 {
			continue
		}
		parsed, err := ParseSchema(string(data))
		if err != nil {
			failed += len(meta.TestVectors)
			total += len(meta.TestVectors)
			failures[fmt.Sprintf("%s: parse: %v", filepath.Base(file), err)]++
			continue
		}
		for _, vector := range meta.TestVectors {
			total++
			payload, err := hexToBytes(vector.Payload)
			if err != nil {
				failed++
				continue
			}
			// Recover per vector: a panic in the decoder is a defect to report, not
			// a reason to abandon the remaining vectors.
			out, err := func() (out map[string]any, err error) {
				defer func() {
					if recovered := recover(); recovered != nil {
						err = fmt.Errorf("panic: %v", recovered)
					}
				}()
				if vector.FPort != nil {
					return parsed.DecodeWithPort(payload, *vector.FPort)
				}
				return parsed.Decode(payload)
			}()
			if err != nil {
				failed++
				failures[fmt.Sprintf("%s: decode: %v", filepath.Base(file), err)]++
				continue
			}
			mismatch := ""
			for key, want := range vector.Expected {
				got, present := out[key]
				if !present {
					mismatch = fmt.Sprintf("%s missing", key)
					break
				}
				if !corpusValuesMatch(want, got) {
					mismatch = fmt.Sprintf("%s: want %v, got %v", key, want, got)
					break
				}
			}
			if mismatch == "" {
				passed++
			} else {
				failed++
				failures[fmt.Sprintf("%s: %s", filepath.Base(file), mismatch)]++
			}
		}
	}

	t.Logf("corpus vectors: %d total, %d passed, %d failed", total, passed, failed)
	shown := 0
	for detail := range failures {
		if shown >= 12 {
			t.Logf("  ... and %d more distinct failures", len(failures)-shown)
			break
		}
		t.Logf("  %s", detail)
		shown++
	}
	if passed < corpusFloor {
		t.Errorf("only %d corpus vectors pass, floor is %d", passed, corpusFloor)
	}
}

// corpusValuesMatch is the comparison the conformance tolerance defines: numeric
// within tolerance, hex literals read as numbers, booleans as 0 and 1. Without this
// the runner reported its own formatting differences as decode failures.
func corpusValuesMatch(want, got any) bool {
	wantNum, wantOK := corpusAsNumber(want)
	gotNum, gotOK := corpusAsNumber(got)
	if wantOK && gotOK {
		return math.Abs(wantNum-gotNum) <= math.Max(0.001, math.Abs(wantNum)*0.001)
	}
	return fmt.Sprintf("%v", want) == fmt.Sprintf("%v", got)
}

func corpusAsNumber(value any) (float64, bool) {
	if number, ok := toFloat64(value); ok {
		return number, true
	}
	switch typed := value.(type) {
	case bool:
		if typed {
			return 1, true
		}
		return 0, true
	case string:
		lower := strings.ToLower(strings.TrimSpace(typed))
		if lower == "true" {
			return 1, true
		}
		if lower == "false" {
			return 0, true
		}
		if strings.HasPrefix(lower, "0x") {
			var parsed int64
			if _, err := fmt.Sscanf(lower[2:], "%x", &parsed); err == nil {
				return float64(parsed), true
			}
		}
	}
	return 0, false
}

func hexToBytes(text string) ([]byte, error) {
	cleaned := strings.NewReplacer(" ", "", "\t", "", "\n", "").Replace(text)
	if len(cleaned)%2 != 0 {
		return nil, fmt.Errorf("odd hex length in %q", text)
	}
	out := make([]byte, 0, len(cleaned)/2)
	for i := 0; i < len(cleaned); i += 2 {
		var value int
		if _, err := fmt.Sscanf(cleaned[i:i+2], "%02x", &value); err != nil {
			return nil, err
		}
		out = append(out, byte(value))
	}
	return out, nil
}
