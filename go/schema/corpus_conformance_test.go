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
	"regexp"
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
// CR-2026-071's three fixtures (arithmetic-order-computed, arithmetic-order-read,
// guard-else-as-declared) decode now that a compute takes its bare modifiers and a failed
// guard's `else` skips the lookup, so the full count is 2363; guard-else-as-declared's two
// further vectors (one per lookup label, for the mutation gate) make it 2365.
// CR-2026-085/086's fixtures decode now that bytes after the last field are reported
// (leftover-bytes, match-default-skip's skipped body) and valid_range is compared before
// the lookup (range-before-lookup's four vectors), so the full count is 2371.
const corpusFloor = 2945 // +3: tlv-one-field-catch-all (CR-2026-194); +3: ascii-utf8 (CR-2026-190); +5: ascii-terminator-or-end (CR-2026-188); +1: time-field date_parts_all_zero (PS-614 amended: omitted, warned); +8: bit-labels (CR-2026-187); +6: repeat-packed-bits (CR-2026-189); +2: time-anchored-history (CR-2026-192); +7: checksum-placement (CR-2026-185); +9: time-field (CR-2026-186); +3: tlv-nested-merge (CR-2026-173); +2: meta-semantic (CR-2026-179); +8: checksum-fields (CR-2026-174, one an expected_error vector); +3: bitfield-string-width-split (CR-2026-183); +11: whole-number-read-in-place (CR-2026-182); +3: definitions-field-and-lookup (CR-2026-168), plain fixed; +9: string-template (CR-2026-176; +5: bytes-encode-exact (CR-2026-181); +14: bitfield-string-padded-signed (CR-2026-170); +7: compute-pow (CR-2026-172); +9: byte-string-reread (CR-2026-175); +10: bytes-format-forms (all plain fixed; every vector round-trips); +11: byte-group-member-var; +1: round-ties-up (CR-2026-171); +22: minifloat-reference-binding (11 types x 2); +13: match-fractional-range-keys (match; every vector round-trips); +4: bitfield-string-field-endian (two fixtures); +7: expected-error (CR-2026-167: three decode vectors expecting a failure, four that decode; its two encode vectors are not decode vectors); +1: repeat-sampled (CR-2026-156); +3: negative-zero (2) and bf16 negative_zero (CR-2026-154); +5: wide-peek-selector, wide-peek-selector-u64; +10: no-silent-empty fixtures (length-selects-layout, literal-selects-branch, tag-entry-constants); +3: match-discriminator-from-inner-case (CR-2026-151); +9: bf16 and bf16's infinity and NaN vectors (CR-2026-149); +4: ascii-numeral (CR-2026-150); +9: bf16 and bf16-specials (CR-2026-149); +12: the four tlv encode-gap fixtures (tlv-object-with-inner-branch, tlv-case-of-one-inline-branch, tlv-float-reading, tlv-scaled-wide-reading); +2: tlv-name-object-and-scalar; +5: match-fractional-discriminator (a fraction matches no single key); +4: mask-reference-binding; +16: CR-2026-109/132 lookup-reference-binding (14 vectors) and tag-component-reference-binding (2); +4: ascii-latin1 (CR-2026-114); +1: CR-2026-128 tlv-repeat-merge-shared-last; +2: CR-2026-125 tlv-repeat-collect-first-case, tlv-repeat-merge-shared-member; +6: CR-2026-110 tlv-repeat-* fixtures; +3: byte-group-width (CR-2026-131); +2: CR-2026-135 fixtures (encoder-rounding, arithmetic-worked-examples); +1: round-large-value just_above_one (mutation gate); +5: CR-2026-121/123 fixtures (repeat-element-null-sentinel, repeat-element-internal-name, repeat-identity-sibling x2, repeat-count-computed); +2: CR-2026-124 guard-set empty in/not_in; +8: match-u32-peek-discriminator and match-u32-inline-discriminator; +4: names-mangling-alike; +2: CR-2026-127 guard-boolean-operand; +2: CR-2026-117 guard-absent-operand; +10: CR-2026-118 fixtures (element-several-readings, element-case-several-readings, present-if-set, guard-set); +8: registry divergence fixtures (tag-component-ref, reference-to-absent, zero-length, numeric-looking-text, name-from-object, round-large-value); +7: CR-2026-111 element repeat and index_base fixtures; +2: flagged-group-field-list; +8: name-from-rendering (CR-2026-112); +2: boolean-literal (CR-2026-107); +14: the nine TS013-defect fixtures (bitfield-string-wide, wide-bit-range, ...); +8: CR-2026-104 lookup-boolean-labels and lookup-number-labels; +3: byte-group-bool; +11: tlv-case-field-list, object-member-field-list, tlv-tag-var-name-from; +5: tlv-case-key-patterns; +1: meta-tlv-channels (CR-2026-097); +9: the CR-2026-096 _meta fixtures (meta-device-context, meta-port-default, meta-producer, meta-nested); dl-atm41g2's two vendor vectors (+2); +40: vendor vectors for the schemas the unit cleanup touches; +121: vendor-codec vectors that kill vobo's mutation survivors; +6: Milesight battery-channel vendor vectors (percent units)

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
	// CR-2026-096: the input context (PS-495) beside the fPort, and the interpreter
	// output's `_meta`, compared exactly where present.
	InputMetadata map[string]any `yaml:"input_metadata"`
	ExpectedMeta  *any           `yaml:"expected_meta"`
	// CR-2026-167 (PS-576): the vector states that its decode fails, and which rule the
	// failure enforces. It stands in place of `expected`.
	ExpectedError *corpusExpectedError `yaml:"expected_error"`
}

// corpusExpectedError is a vector's `expected_error`: `requirement` is one PS number or a
// list of them, `at` the field the error names (PS-576).
type corpusExpectedError struct {
	Requirement any    `yaml:"requirement"`
	At          string `yaml:"at"`
}

var corpusPSNumber = regexp.MustCompile(`PS-\d+[a-z]?`)

// corpusErrorMismatch says why an error does not satisfy `expected_error`, or "" where it
// does (PS-576). The PS numbers an error cites must include every requirement listed -
// checked only where it cites any, since an implementation need not say which rule it
// enforced - and `at` must be named in the message, quoted, or be a JSON Pointer it
// contains. Wording is never compared.
func corpusErrorMismatch(message string, want *corpusExpectedError) string {
	var wanted []string
	switch r := want.Requirement.(type) {
	case string:
		wanted = []string{r}
	case []any:
		for _, item := range r {
			wanted = append(wanted, fmt.Sprint(item))
		}
	}
	cited := map[string]bool{}
	for _, id := range corpusPSNumber.FindAllString(message, -1) {
		cited[id] = true
	}
	if len(cited) > 0 {
		for _, id := range wanted {
			if !cited[id] {
				return fmt.Sprintf("the error cites %v, not %v: %s", cited, wanted, message)
			}
		}
	}
	if want.At != "" && !strings.Contains(message, "'"+want.At+"'") &&
		!(strings.HasPrefix(want.At, "/") && strings.Contains(message, want.At)) {
		return fmt.Sprintf("the error does not name %q: %s", want.At, message)
	}
	return ""
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
	root := repoPath(t, "schemas", "devices")
	if override := os.Getenv("CORPUS_ROOT"); override != "" {
		root = override // see corpus_report_test.go
	}
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
	report := newCorpusReport()
	metaReport := newCorpusMetaReport()

	for _, file := range files {
		rel, _ := filepath.Rel(root, file)
		rel = corpusRelative(rel)
		if !report.includes(rel) {
			continue
		}
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
			for index, vector := range meta.TestVectors {
				if vector.Payload != "" {
					metaReport.add(rel, index, vector.Name, nil)
				}
			}
			failed += len(meta.TestVectors)
			total += len(meta.TestVectors)
			failures[fmt.Sprintf("%s: parse: %v", filepath.Base(file), err)]++
			for index, vector := range meta.TestVectors {
				if vector.Payload == "" {
					report.add(rel, index, vector.Name, "skip", "no payload (encode vector)")
				} else {
					report.add(rel, index, vector.Name, "error", fmt.Sprintf("parse: %v", err))
				}
			}
			continue
		}
		for index, vector := range meta.TestVectors {
			// An encode vector carries the values to encode and no payload to decode
			// (PS-047). Counting it as a failed decode would be wrong twice: it was
			// never decoded, and tools/vector-verdicts.py already runs it on both
			// conformance paths.
			if vector.Payload == "" {
				report.add(rel, index, vector.Name, "skip", "no payload (encode vector)")
				continue
			}
			total++
			payload, err := hexToBytes(vector.Payload)
			if err != nil {
				failed++
				report.add(rel, index, vector.Name, "error", fmt.Sprintf("payload: %v", err))
				metaReport.add(rel, index, vector.Name, nil)
				continue
			}
			// CR-2026-096: the interpreter output, for every vector the meta report
			// covers and for those that pin their `_meta`.
			var interpreted map[string]any
			var interpretErr error
			if metaReport.enabled() || vector.ExpectedMeta != nil {
				interpreted, interpretErr = corpusInterpret(parsed, vector, payload)
				var produced, record any
				if interpretErr == nil {
					produced, record = interpreted["_meta"], interpreted["_decode"]
				}
				metaReport.addWithRecord(rel, index, vector.Name, produced, record)
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
			if vector.ExpectedError != nil {
				// PS-576: the decode must fail, and its error must meet the vector.
				problem := "succeeded where an error was expected"
				if err != nil {
					problem = corpusErrorMismatch(err.Error(), vector.ExpectedError)
				}
				if problem == "" {
					passed++
					report.add(rel, index, vector.Name, "pass", "")
				} else {
					failed++
					failures[fmt.Sprintf("%s: %s", filepath.Base(file), problem)]++
					report.add(rel, index, vector.Name, "fail", problem)
				}
				continue
			}
			if err != nil {
				failed++
				failures[fmt.Sprintf("%s: decode: %v", filepath.Base(file), err)]++
				report.add(rel, index, vector.Name, "error", fmt.Sprintf("decode: %v", err))
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
				if want == nil {
					// PS-043 (CR-2026-075): a null expectation asserts the key is absent.
					if present {
						mismatch = fmt.Sprintf("%s: reported %v, expected absent", key, got)
						break
					}
					continue
				}
				if !present {
					mismatch = fmt.Sprintf("%s missing", key)
					break
				}
				if !corpusValuesMatch(want, got) {
					mismatch = fmt.Sprintf("%s: want %v, got %v", key, want, got)
					break
				}
			}
			if mismatch == "" && vector.ExpectedMeta != nil {
				if interpretErr != nil {
					mismatch = fmt.Sprintf("interpret: %v", interpretErr)
				} else if ok, detail := corpusMetaMatches(*vector.ExpectedMeta, interpreted["_meta"], "_meta"); !ok {
					mismatch = detail
				}
			}
			if mismatch == "" {
				passed++
				report.add(rel, index, vector.Name, "pass", "")
			} else {
				failed++
				failures[fmt.Sprintf("%s: %s", filepath.Base(file), mismatch)]++
				report.add(rel, index, vector.Name, "fail", mismatch)
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
	report.write(t)
	metaReport.write(t)
	if report.restricted() {
		t.Logf("CORPUS_ONLY/CORPUS_ROOT is set: %d schema(s) selected, floor %d not applied", len(report.only), corpusFloor)
	} else if passed < corpusFloor {
		t.Errorf("only %d corpus vectors pass, floor is %d", passed, corpusFloor)
	}
}

// corpusValuesMatch is the comparison the conformance tolerance defines: numeric
// within tolerance, hex literals read as numbers, booleans as 0 and 1. Without this
// the runner reported its own formatting differences as decode failures.
//
// A mapping or list is compared member by member (PS-044, PS-045), as the Python, Java and
// C# runners do: a nested null asserts absence (PS-043) and unlisted keys are not checked.
// Nested values used to be compared by their printed form, so a nested null expectation
// could not pass and a nested float had no tolerance.
func corpusValuesMatch(want, got any) bool {
	if wantMap, ok := want.(map[string]any); ok {
		gotMap, ok := got.(map[string]any)
		if !ok {
			return fmt.Sprintf("%v", want) == fmt.Sprintf("%v", got)
		}
		for key, member := range wantMap {
			value, present := gotMap[key]
			if member == nil {
				if present {
					return false
				}
				continue
			}
			if !present || !corpusValuesMatch(member, value) {
				return false
			}
		}
		return true
	}
	if wantList, ok := want.([]any); ok {
		gotList, ok := got.([]any)
		if !ok || len(gotList) != len(wantList) {
			return fmt.Sprintf("%v", want) == fmt.Sprintf("%v", got)
		}
		for i := range wantList {
			// PS-043 as amended (CR-2026-111): null at a position of an `element`
			// repeat's array asserts null was reported there, not absence.
			if wantList[i] == nil {
				if gotList[i] != nil {
					return false
				}
				continue
			}
			if !corpusValuesMatch(wantList[i], gotList[i]) {
				return false
			}
		}
		return true
	}
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

// corpusInputContext is the vector's input context (PS-495): its fPort, else
// input_metadata's, and input_metadata's recvTime and devEUI.
func corpusInputContext(vector corpusVector) InputContext {
	in := InputContext{FPort: vector.port()}
	if in.FPort == nil {
		if port, ok := vector.InputMetadata["fPort"].(int); ok {
			in.FPort = &port
		}
	}
	in.RecvTime = vector.InputMetadata["recvTime"]
	if eui, present := vector.InputMetadata["devEUI"]; present && eui != nil {
		in.DevEUI = fmt.Sprintf("%v", eui)
	}
	return in
}

// corpusInterpret runs Interpret for a vector, recovering a panic as an error.
func corpusInterpret(parsed *Schema, vector corpusVector, payload []byte) (out map[string]any, err error) {
	defer func() {
		if recovered := recover(); recovered != nil {
			err = fmt.Errorf("panic: %v", recovered)
		}
	}()
	in := corpusInputContext(vector)
	in.DecodeRecord = true // CR-2026-162: the report carries `_decode` beside `_meta`
	return parsed.Interpret(payload, in)
}

// corpusMetaMatches is validate_schema.meta_matches: `_meta` exactly as expected. The
// same keys at every level and no others; numbers by value (5 equals 5.0); a boolean is
// never a number; lists elementwise in order.
func corpusMetaMatches(want, got any, path string) (bool, string) {
	if wantMap := asStringMap(want); wantMap != nil {
		gotMap := asStringMap(got)
		if gotMap == nil {
			return false, fmt.Sprintf("%s: expected an object, got %v", path, got)
		}
		var missing, extra []string
		for key := range wantMap {
			if _, ok := gotMap[key]; !ok {
				missing = append(missing, key)
			}
		}
		for key := range gotMap {
			if _, ok := wantMap[key]; !ok {
				extra = append(extra, key)
			}
		}
		if len(missing) > 0 || len(extra) > 0 {
			sort.Strings(missing)
			sort.Strings(extra)
			return false, fmt.Sprintf("%s: missing %v, unexpected %v", path, missing, extra)
		}
		keys := make([]string, 0, len(wantMap))
		for key := range wantMap {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if ok, detail := corpusMetaMatches(wantMap[key], gotMap[key], path+"."+key); !ok {
				return ok, detail
			}
		}
		return true, ""
	}
	if wantList, ok := want.([]any); ok {
		gotList, ok := got.([]any)
		if !ok || len(gotList) != len(wantList) {
			return false, fmt.Sprintf("%s: expected %v, got %v", path, want, got)
		}
		for i := range wantList {
			if ok, detail := corpusMetaMatches(wantList[i], gotList[i], fmt.Sprintf("%s[%d]", path, i)); !ok {
				return ok, detail
			}
		}
		return true, ""
	}
	_, wantBool := want.(bool)
	_, gotBool := got.(bool)
	var ok bool
	if wantBool || gotBool {
		ok = wantBool && gotBool && want == got
	} else {
		wantNum, wantIsNum := toFloat64(want)
		gotNum, gotIsNum := toFloat64(got)
		if wantIsNum && gotIsNum {
			ok = wantNum == gotNum
		} else {
			ok = !wantIsNum && !gotIsNum &&
				fmt.Sprintf("%T:%v", want, want) == fmt.Sprintf("%T:%v", got, got)
		}
	}
	if ok {
		return true, ""
	}
	return false, fmt.Sprintf("%s: expected %v, got %v", path, want, got)
}
