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
// Go decodes the whole corpus, so this is the full count and any failure is a
// regression rather than a known gap. The three LoRaWAN frame vectors that used to
// fail here needed the sequential bitfield form `u8:3`, which CR-2026-006 withdrew
// in favour of the bracket form `u8[5:7]` this interpreter already had.
// CR-2026-007 settled the floored `idiv`/`mod` convention and this interpreter
// implements it, so the negative-operand vectors that used to be excluded now pass
// and the floor is the full corpus again.
// CR-2026-014's `expected_warnings` added three fixtures for the `unknown` parameter,
// which no device schema sets, and the floor had drifted 29 below the full count as
// vectors were added without it being raised. It is the full count again: 1222.
// CR-2026-020 brought the five implementations onto the same `match`, CR-2026-021 the
// same repeat `max`, and CR-2026-022 the same byte_length span, so their fixtures
// pass everywhere and the full count is 1237.
// CR-2026-031 added the name_from var-mismatch fixture, whose two vectors decode
// everywhere, so the full count is 1239.
const corpusFloor = 1242

type corpusVector struct {
	Name    string `yaml:"name"`
	Payload string `yaml:"payload"`
	FPort   *int   `yaml:"fPort"`
	// Both spellings occur in the corpus. Reading only fPort meant a port-based
	// schema was decoded with no port at all, so every field of it was reported
	// missing - a runner defect that looked like an interpreter gap.
	FPortLower *int           `yaml:"fport"`
	Expected   map[string]any `yaml:"expected"`
	// PS-305 to PS-308: what the decode must say, as well as what it must read. A
	// pointer rather than a slice, because absent and `[]` mean different things -
	// absent asserts nothing, `[]` asserts that no warning was reported, which is the
	// form that catches a schema edit beginning to discard data - and both unmarshal
	// to an empty slice.
	// An entry is a string or a list of strings, all of which must appear in that one
	// warning (PS-306) - the tag and the byte count are not contiguous in any
	// implementation's text - so the element type is `any`.
	ExpectedWarnings *[]any `yaml:"expected_warnings"`
}

// corpusWarningsMismatch reports how the warnings a decode produced differ from what the
// vector expects, or "" where they agree or the vector asserts nothing (PS-308).
//
// Entries are matched as substrings and positionally: the specification fixes what a
// warning must contain, not its wording (PS-306), and the list is complete rather than a
// subset (PS-305), so an unexpected warning fails just as a missing one does.
func corpusWarningsMismatch(vector corpusVector, out map[string]any) string {
	if vector.ExpectedWarnings == nil {
		return ""
	}
	want := *vector.ExpectedWarnings
	var got []string
	if reported, present := out["_warnings"]; present {
		if list, ok := reported.([]string); ok {
			got = list
		}
	}
	if len(got) != len(want) {
		return fmt.Sprintf("expected %d warning(s), got %d: %v", len(want), len(got), got)
	}
	for i, entry := range want {
		for _, fragment := range corpusWarningFragments(entry) {
			if !strings.Contains(got[i], fragment) {
				return fmt.Sprintf("warning[%d]: %q not found in %q", i, fragment, got[i])
			}
		}
	}
	return ""
}

// corpusWarningFragments reads one `expected_warnings` entry, which is a string or a list
// of strings all of which must appear in the same warning (PS-306).
func corpusWarningFragments(entry any) []string {
	switch value := entry.(type) {
	case string:
		return []string{value}
	case []any:
		fragments := make([]string, 0, len(value))
		for _, part := range value {
			fragments = append(fragments, fmt.Sprintf("%v", part))
		}
		return fragments
	default:
		return []string{fmt.Sprintf("%v", value)}
	}
}

// port returns the vector's fPort under either spelling.
func (v corpusVector) port() *int {
	if v.FPort != nil {
		return v.FPort
	}
	return v.FPortLower
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
			// An encode vector carries the values to encode and no payload to decode
			// (PS-047). Counting it as a failed decode would be wrong twice: it was
			// never decoded, and tools/vector-verdicts.py already runs it on both
			// conformance paths.
			if vector.Payload == "" {
				continue
			}
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
				if fport := vector.port(); fport != nil {
					return parsed.DecodeWithPort(payload, *fport)
				}
				return parsed.Decode(payload)
			}()
			if err != nil {
				failed++
				failures[fmt.Sprintf("%s: decode: %v", filepath.Base(file), err)]++
				continue
			}
			mismatch := ""
			if problem := corpusWarningsMismatch(vector, out); problem != "" {
				mismatch = problem
			}
			for key, want := range vector.Expected {
				if mismatch != "" {
					break
				}
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
		// PS-039: an integer expectation must match exactly. PS-040's 0.001 is for
		// floats, and it is absolute. This used to be a relative
		// max(0.001, |want|*0.001) applied to everything, which on a GPS timestamp
		// is about 20 days of slack - it is how a ts003 vector wrong by 2048
		// seconds passed here while the composed-corpus tool rejected it.
		if corpusWantsInteger(want) {
			return wantNum == gotNum
		}
		return math.Abs(wantNum-gotNum) <= 0.001
	}
	return fmt.Sprintf("%v", want) == fmt.Sprintf("%v", got)
}

// corpusWantsInteger reports whether the vector wrote its expected value as an
// integer, which is what selects exact comparison. A decoded value arriving as a
// float64 does not make the expectation a float: every interpreter here widens
// integers on the way out.
func corpusWantsInteger(want any) bool {
	switch typed := want.(type) {
	case int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64:
		return true
	case string:
		text := strings.ToLower(strings.TrimSpace(typed))
		if strings.HasPrefix(text, "0x") {
			return true
		}
		if text == "" || strings.ContainsAny(text, ".eE") {
			return false
		}
		var parsed int64
		_, err := fmt.Sscanf(text, "%d", &parsed)
		return err == nil
	default:
		return false
	}
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
