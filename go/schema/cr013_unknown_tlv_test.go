package schema

import (
	"encoding/hex"
	"strings"
	"testing"
)

// CR-2026-013: an unknown TLV tag has to be visible.
//
// `unknown` already had three modes; what was missing was any way for a caller to tell
// that the default had fired. This decoder collected warnings in the context and reported
// none of them, so a payload that stopped at an undescribed tag came back as a successful
// decode carrying fewer fields - indistinguishable from a device that sent fewer fields.
// `raw` was worse: it behaved as `skip`, building nothing at all.

const (
	// Tag 0x01 carries a u16 of 60; tag 0x09 is not described. Tag-only, so nothing
	// delimits the unknown entry and the two trailing bytes cannot be reached.
	cr013TagOnly = "01003C090BB8"
	// The same with a length byte after each tag, so the entry can be stepped over.
	cr013Delimited = "0102003C09020BB8"
)

func cr013Schema(mode string, lengthSize int) string {
	yaml := "name: t\nendian: big\nfields:\n  - tlv:\n      tag_size: 1\n"
	if mode != "" {
		yaml += "      unknown: " + mode + "\n"
	}
	if lengthSize > 0 {
		yaml += "      length_size: 1\n"
	}
	return yaml + "      cases:\n        1:\n          - {name: known, type: u16}\n"
}

func cr013Decode(t *testing.T, mode string, lengthSize int, payload string) (map[string]any, error) {
	t.Helper()
	s, err := ParseSchema(cr013Schema(mode, lengthSize))
	if err != nil {
		t.Fatalf("ParseSchema: %v", err)
	}
	raw, err := hex.DecodeString(payload)
	if err != nil {
		t.Fatalf("bad payload %q: %v", payload, err)
	}
	return s.Decode(raw)
}

func cr013Warnings(t *testing.T, out map[string]any) []string {
	t.Helper()
	value, present := out["_warnings"]
	if !present {
		return nil
	}
	warnings, ok := value.([]string)
	if !ok {
		t.Fatalf("_warnings is %T, want []string", value)
	}
	return warnings
}

func TestCR2026013StoppingShortIsReported(t *testing.T) {
	// PS-301, PS-302. The fields before the tag are still reported, which is why this is
	// a warning and not an error.
	out, err := cr013Decode(t, "skip", 0, cr013TagOnly)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out["known"] != uint64(60) && out["known"] != int64(60) {
		t.Errorf("known = %v (%T), want 60", out["known"], out["known"])
	}
	want := "unknown TLV tag (0x09) at offset 3: 3 of 6 byte(s) left undecoded"
	got := cr013Warnings(t, out)
	if len(got) != 1 || got[0] != want {
		t.Errorf("warnings = %q, want [%q]", got, want)
	}
}

func TestCR2026013SkipIsTheDefault(t *testing.T) {
	// A schema that sets nothing behaves as one that sets `skip`.
	unset, err := cr013Decode(t, "", 0, cr013TagOnly)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	explicit, err := cr013Decode(t, "skip", 0, cr013TagOnly)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if strings.Join(cr013Warnings(t, unset), "|") != strings.Join(cr013Warnings(t, explicit), "|") {
		t.Errorf("unset %q differs from skip %q",
			cr013Warnings(t, unset), cr013Warnings(t, explicit))
	}
}

func TestCR2026013ADelimitedEntryIsSteppedOverAndReported(t *testing.T) {
	out, err := cr013Decode(t, "skip", 1, cr013Delimited)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	want := "unknown TLV tag (0x09) skipped, 2 byte(s) discarded"
	got := cr013Warnings(t, out)
	if len(got) != 1 || got[0] != want {
		t.Errorf("warnings = %q, want [%q]", got, want)
	}
}

func TestCR2026013ACleanDecodeCarriesNoWarningKey(t *testing.T) {
	out, err := cr013Decode(t, "skip", 0, "01003C")
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, present := out["_warnings"]; present {
		t.Errorf("_warnings present on a clean decode: %v", out["_warnings"])
	}
}

func TestCR2026013ErrorModeFailsNamingTheTag(t *testing.T) {
	_, err := cr013Decode(t, "error", 0, cr013TagOnly)
	if err == nil {
		t.Fatal("expected an error")
	}
	if !strings.Contains(err.Error(), "0x09") {
		t.Errorf("error %q does not name the tag", err)
	}
}

func TestCR2026013RawReportsItsEntryWhenOutputIsMerged(t *testing.T) {
	// PS-303. `merge` defaults to true, so this is every schema that does not set it;
	// before this the entry was never built at all.
	out, err := cr013Decode(t, "raw", 0, cr013TagOnly)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	entries, ok := out["unknown_tags"].([]map[string]any)
	if !ok || len(entries) != 1 {
		t.Fatalf("unknown_tags = %v (%T), want one entry", out["unknown_tags"], out["unknown_tags"])
	}
	if entries[0]["raw"] != "0bb8" {
		t.Errorf("raw = %v, want 0bb8", entries[0]["raw"])
	}
}

func TestCR2026013ADelimitedRawCaptureTakesOnlyItsOwnBytes(t *testing.T) {
	out, err := cr013Decode(t, "raw", 1, cr013Delimited)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	entries, ok := out["unknown_tags"].([]map[string]any)
	if !ok || len(entries) != 1 {
		t.Fatalf("unknown_tags = %v, want one entry", out["unknown_tags"])
	}
	if entries[0]["raw"] != "0bb8" {
		t.Errorf("raw = %v, want 0bb8", entries[0]["raw"])
	}
	if got := cr013Warnings(t, out); len(got) != 0 {
		t.Errorf("warnings = %q, want none: the entry was delimited", got)
	}
}

func TestCR2026013TheWarningNamesEveryTagComponent(t *testing.T) {
	// The Milesight shape: channel then type. Both are needed to add the missing case.
	s, err := ParseSchema("name: t\nendian: big\nfields:\n  - tlv:\n" +
		"      tag_fields:\n        - {name: channel, type: u8}\n" +
		"        - {name: kind, type: u8}\n" +
		"      tag_key: [channel, kind]\n" +
		"      cases:\n        \"[1, 117]\":\n          - {name: battery, type: u8}\n")
	if err != nil {
		t.Fatalf("ParseSchema: %v", err)
	}
	raw, _ := hex.DecodeString("0175640569")
	out, err := s.Decode(raw)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	got := cr013Warnings(t, out)
	if len(got) != 1 || !strings.Contains(got[0], "0x05, 0x69") {
		t.Errorf("warnings = %q, want one naming (0x05, 0x69)", got)
	}
}
