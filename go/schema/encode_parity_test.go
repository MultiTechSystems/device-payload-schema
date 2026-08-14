package schema

import (
	"strings"
	"testing"
)

// Two silent failures in the Go encoder, both fixed by bringing it onto what
// Python, Java and C# already do. Each was a success return that produced a
// payload no device could read.

const missingFieldSchema = `
name: three-fields
endian: big
fields:
  - name: first
    type: u16
  - name: second
    type: u8
  - name: third
    type: u16
`

// A field the input omits is written as zero, so the fields after it stay where
// the device expects them.
//
// Skipping it wrote no bytes at all: the payload came out short and everything
// following the gap shifted down by the width of the missing field. `third` was
// then read out of `second`'s byte and one byte of its own, which is a corrupt
// frame returned as a success.
func TestMissingFieldIsZeroFilledNotSkipped(t *testing.T) {
	s, err := ParseSchema(missingFieldSchema)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	payload, err := s.Encode(map[string]any{"first": 0x1111, "third": 0x3333})
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}

	// 5 bytes: u16 + u8 + u16, with the omitted `second` as a zero placeholder.
	want := []byte{0x11, 0x11, 0x00, 0x33, 0x33}
	if string(payload) != string(want) {
		t.Fatalf("payload = % x, want % x", payload, want)
	}

	// The point of the placeholder: `third` decodes back to what was asked for.
	decoded, err := s.Decode(payload)
	if err != nil {
		t.Fatalf("decode failed: %v", err)
	}
	if got, _ := toFloat64(decoded["third"]); got != 0x3333 {
		t.Errorf("third round-tripped as %v, want %d — the payload is misaligned",
			decoded["third"], 0x3333)
	}
}

// Zero is a placeholder, not a value the caller supplied, so it is reported.
func TestMissingFieldIsReported(t *testing.T) {
	s, err := ParseSchema(missingFieldSchema)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	result, err := s.EncodeToResult(map[string]any{"first": 1, "third": 3}, 0)
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}
	if len(result.Warnings) != 1 || !strings.Contains(result.Warnings[0], "second") {
		t.Errorf("warnings = %v, want one naming the omitted field %q",
			result.Warnings, "second")
	}

	// The wording matches Python, Java and C#, so a cross-language diff of
	// warnings compares equal strings.
	if want := "Missing field: second"; result.Warnings[0] != want {
		t.Errorf("warning = %q, want %q", result.Warnings[0], want)
	}

	// Nothing was supplied on the caller's behalf here, so there is nothing to say.
	full, err := s.EncodeToResult(map[string]any{"first": 1, "second": 2, "third": 3}, 0)
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}
	if len(full.Warnings) != 0 {
		t.Errorf("warnings = %v, want none when every field is supplied", full.Warnings)
	}
}

// A field with no name and an `_`-prefixed intermediate both occupy bytes on the
// wire that the caller never supplies. Returning early wrote none of them, so a
// top-level `skip` or a `_reserved` byte shifted every field after it.
//
// No corpus vector covers this: of the five schemas with such a field at top
// level, four ship no test vectors at all and the fifth
// (_language-conformance/skip-type.yaml) names its skip field, which the missing
// field path above already handles. So this test is the only guard.
func TestUnnamedAndInternalFieldsStillOccupyTheirBytes(t *testing.T) {
	s, err := ParseSchema(`
name: padded
endian: big
fields:
  - name: a
    type: u8
  - type: skip
    length: 2
  - name: _reserved
    type: u8
  - name: b
    type: u8
`)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	payload, err := s.Encode(map[string]any{"a": 1, "b": 2})
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}

	// Five bytes: a, two skipped, one reserved, b. Previously `01 02`, which put
	// b where the device reads the first skipped byte.
	want := []byte{0x01, 0x00, 0x00, 0x00, 0x02}
	if string(payload) != string(want) {
		t.Fatalf("payload = % x, want % x", payload, want)
	}

	decoded, err := s.Decode(payload)
	if err != nil {
		t.Fatalf("decode failed: %v", err)
	}
	if got, _ := toFloat64(decoded["b"]); got != 2 {
		t.Errorf("b round-tripped as %v, want 2 — the padding is not accounted for",
			decoded["b"])
	}

	// Neither is a value the caller withheld, so neither is warned about.
	result, err := s.EncodeToResult(map[string]any{"a": 1, "b": 2}, 0)
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}
	if len(result.Warnings) != 0 {
		t.Errorf("warnings = %v, want none: padding and internals are not the "+
			"caller's to supply", result.Warnings)
	}
}

// A computed field is very often the `_`-prefixed kind — mclimate/vicki has six —
// and occupies no bytes of its own. The `number` check therefore has to run
// before the name check: with the order reversed these reach encodeField, whose
// switch has no `number` case, and a schema that encoded fine starts reporting
// "cannot encode type".
func TestInternalComputedFieldsStillOccupyNoBytes(t *testing.T) {
	s, err := ParseSchema(`
name: derived
endian: big
fields:
  - name: raw
    type: u8
  - name: _doubled
    type: number
    ref: raw
    mult: 2
  - name: tail
    type: u8
`)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	payload, err := s.Encode(map[string]any{"raw": 1, "tail": 2})
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}
	if want := []byte{0x01, 0x02}; string(payload) != string(want) {
		t.Errorf("payload = % x, want % x — a computed field must contribute nothing",
			payload, want)
	}
}

// encodeFields and encodeFieldList were two hand-maintained copies of one
// dispatch, and merging them routed the tlv/match/repeat path through the
// constructs only the top-level copy dispatched on.
//
// For `repeat` the bytes were already right: encodeFieldList had no branch for
// it, but the field fell through to encodeField, whose TypeRepeat case walks the
// records the same way. What differs is what happens to input that is not a list
// of records — encodeField writes nothing and returns success, encodeRepeat says
// so. That is the "silent write of nothing" shape, so the strict path is the one
// worth having everywhere.
func TestRepeatInsideATLVCaseEncodes(t *testing.T) {
	s, err := ParseSchema(`
name: tlv-with-repeat
endian: big
fields:
  - tlv:
      tag_fields:
        - name: channel_id
          type: u8
      tag_key: [channel_id]
      cases:
        "9":
          - name: samples
            type: repeat
            until: end
            fields:
              - name: value
                type: u8
`)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	payload, err := s.Encode(map[string]any{
		"samples": []any{
			map[string]any{"value": 0x0a},
			map[string]any{"value": 0x14},
		},
	})
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}
	// Tag 09 then the two records.
	if want := []byte{0x09, 0x0a, 0x14}; string(payload) != string(want) {
		t.Errorf("payload = % x, want % x", payload, want)
	}

	// A repeat whose value is not a list of records is now reported from inside a
	// tlv case, where it used to write nothing and return success.
	_, err = s.Encode(map[string]any{"samples": 42})
	if err == nil {
		t.Error("a non-list repeat value encoded without complaint")
	} else if !strings.Contains(err.Error(), "list of records") {
		t.Errorf("error = %v, want it to name the expected shape", err)
	}
}

// A tlv case is encoded into its own buffer so its length prefix can be measured
// before it is written. Warnings raised in there belong to the same encode, so
// the branch merges them back — without that they were silently dropped, which
// would have made the tlv path look clean by construction.
func TestWarningsEscapeATLVCase(t *testing.T) {
	s, err := ParseSchema(`
name: tlv-warning
endian: big
fields:
  - tlv:
      tag_fields:
        - name: channel_id
          type: u8
      tag_key: [channel_id]
      cases:
        "3":
          - name: temperature
            type: u8
          - name: humidity
            type: u8
`)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	// humidity is omitted, so the case encodes a zero for it.
	result, err := s.EncodeToResult(map[string]any{"temperature": 20}, 0)
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}
	if want := []byte{0x03, 0x14, 0x00}; string(result.Payload) != string(want) {
		t.Fatalf("payload = % x, want % x", result.Payload, want)
	}
	if len(result.Warnings) != 1 || !strings.Contains(result.Warnings[0], "humidity") {
		t.Errorf("warnings = %v, want one naming humidity from inside the tlv case",
			result.Warnings)
	}
}

// A tlv case whose fields sit inside a nameless construct claimed nothing, so the
// case was never a candidate and the channel encoded to no bytes *and no error*.
//
// encodeTLV collected a case's claimable names from the top level of the case
// only, and a byte_group or flagged field has no name there — its names are in its
// group's fields, which encodeByteGroup and encodeFlagged read out of the same
// flat map. hbi/mla20's case 32 is two byte_groups of exactly this shape.
func TestNamelessConstructsInATLVCaseAreClaimed(t *testing.T) {
	t.Run("byte_group", func(t *testing.T) {
		s, err := ParseSchema(`
name: tlv-bytegroup
endian: big
fields:
  - tlv:
      tag_fields:
        - name: channel_id
          type: u8
      tag_key: [channel_id]
      cases:
        "32":
          - byte_group:
              size: 1
              fields:
                - name: charger_status
                  type: u8[0:1]
                - name: device_status
                  type: u8[4:7]
`)
		if err != nil {
			t.Fatalf("parse failed: %v", err)
		}

		payload, err := s.Encode(map[string]any{"charger_status": 1, "device_status": 5})
		if err != nil {
			t.Fatalf("encode failed: %v", err)
		}
		// Tag 0x20, then the two ranges packed into their shared byte:
		// device_status 5 at bits 4-7, charger_status 1 at bits 0-1.
		if want := []byte{0x20, 0x51}; string(payload) != string(want) {
			t.Errorf("payload = % x, want % x", payload, want)
		}
	})

	t.Run("flagged", func(t *testing.T) {
		s, err := ParseSchema(`
name: tlv-flagged
endian: big
fields:
  - tlv:
      tag_fields:
        - name: channel_id
          type: u8
      tag_key: [channel_id]
      cases:
        "7":
          - flagged:
              field: flags
              groups:
                - bit: 0
                  fields:
                    - name: alpha
                      type: u8
                - bit: 1
                  fields:
                    - name: beta
                      type: u8
`)
		if err != nil {
			t.Fatalf("parse failed: %v", err)
		}

		payload, err := s.Encode(map[string]any{"alpha": 0x11, "beta": 0x22})
		if err != nil {
			t.Fatalf("encode failed: %v", err)
		}
		if want := []byte{0x07, 0x11, 0x22}; string(payload) != string(want) {
			t.Errorf("payload = % x, want % x", payload, want)
		}

		// A flagged group whose fields are absent contributes nothing, so the
		// case is still claimed on the one that is present.
		partial, err := s.Encode(map[string]any{"alpha": 0x11})
		if err != nil {
			t.Fatalf("encode failed: %v", err)
		}
		if want := []byte{0x07, 0x11}; string(partial) != string(want) {
			t.Errorf("partial payload = % x, want % x", partial, want)
		}
	})
}

// The counterpart the claiming must not break: a nested object *does* carry a name
// of its own, and its members live in a nested map under that name rather than in
// the case's map. Claiming its members would claim names the map does not have, so
// claimableFields stops at the object. Seventeen corpus cases rely on this.
func TestNestedObjectInATLVCaseIsClaimedByItsOwnName(t *testing.T) {
	s, err := ParseSchema(`
name: tlv-object
endian: big
fields:
  - tlv:
      tag_fields:
        - name: channel_id
          type: u8
      tag_key: [channel_id]
      cases:
        "12":
          - name: record
            type: object
            fields:
              - name: value
                type: u8
`)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	names := claimableFields(s.Fields[0].TLVInline.TLVCases["12"], nil)
	if len(names) != 1 || names[0].Name != "record" {
		got := make([]string, 0, len(names))
		for _, f := range names {
			got = append(got, f.Name)
		}
		t.Fatalf("claimable = %v, want just the object's own name [record]", got)
	}

	payload, err := s.Encode(map[string]any{
		"record": map[string]any{"value": 0x2a},
	})
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}
	if want := []byte{0x0c, 0x2a}; string(payload) != string(want) {
		t.Errorf("payload = % x, want % x", payload, want)
	}
}

const portsNoDefaultSchema = `
name: ports-no-default
endian: big
ports:
  "10":
    direction: downlink
    fields:
      - name: interval
        type: u16
`

const portsWithDefaultSchema = `
name: ports-with-default
endian: big
ports:
  default:
    direction: uplink
    fields:
      - name: temperature
        type: s16
      - name: humidity
        type: u8
  "10":
    direction: downlink
    fields:
      - name: interval
        type: u16
`

// EncodeWithPort discarded the error from ResolveFields, so a port the schema
// never declared encoded to an empty payload and reported success. Python's
// _resolve_fields raises and its encode does not catch it.
func TestEncodeReportsUndeclaredPort(t *testing.T) {
	s, err := ParseSchema(portsNoDefaultSchema)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	payload, err := s.EncodeWithPort(map[string]any{"interval": 600}, 7)
	if err == nil {
		t.Fatalf("encoded % x on undeclared port 7, want an error", payload)
	}
	if !strings.Contains(err.Error(), "no port definition") {
		t.Errorf("error = %v, want it to name the missing port definition", err)
	}

	// The declared port still works.
	if _, err := s.EncodeWithPort(map[string]any{"interval": 600}, 10); err != nil {
		t.Errorf("declared port 10 failed: %v", err)
	}

	// Both entry points shared the defect, because they were copies.
	if _, err := s.EncodeOrderedWithPort(map[string]any{"interval": 600}, 7, nil); err == nil {
		t.Error("EncodeOrderedWithPort accepted undeclared port 7")
	}
}

// What the fix above does *not* cover, stated so nobody assumes otherwise: a
// schema declaring a `default` port resolves every undeclared port to it, so
// encoding against the wrong port still succeeds with plausible bytes for the
// wrong message. A caller needing a specific port has to check Ports itself.
func TestUndeclaredPortStillFallsBackToDefault(t *testing.T) {
	s, err := ParseSchema(portsWithDefaultSchema)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	// Port 7 is undeclared, and the payload it produces is the *uplink* layout.
	payload, err := s.EncodeWithPort(map[string]any{"temperature": 100, "humidity": 50}, 7)
	if err != nil {
		t.Fatalf("encode on port 7 failed: %v", err)
	}
	if len(payload) != 3 {
		t.Errorf("port 7 encoded %d byte(s) (% x), want the 3 of the default port",
			len(payload), payload)
	}
}

// A ports-only schema resolves an unknown port to `default`, and EncodeToResult
// carries the warnings through the same path EncodeWithPort takes.
func TestEncodeToResultCarriesWarningsOnPortedSchema(t *testing.T) {
	s, err := ParseSchema(portsWithDefaultSchema)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}

	result, err := s.EncodeToResult(map[string]any{"temperature": 100}, 0)
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}
	if len(result.Payload) != 3 {
		t.Errorf("payload = % x, want 3 bytes with humidity zero-filled", result.Payload)
	}
	if len(result.Warnings) != 1 || !strings.Contains(result.Warnings[0], "humidity") {
		t.Errorf("warnings = %v, want one naming humidity", result.Warnings)
	}
}
