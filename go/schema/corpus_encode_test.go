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
	"bytes"
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
// CR-2026-024 packed a bare run of bit ranges the way byte_group was already packed, so
// the three LoRaWAN header schemas round-trip and several tlv cases built from bit ranges
// do too: `plain fixed` rises from 55 to 58, `tlv` from 906 to 910, the total to 1155.
// CR-2026-026 gave this encoder the word-ordered u32le16/s32le16 case it never had, and
// rounded its integer conversions instead of truncating them, so the fourteen
// `word_ordered_sensor_id` vectors round-trip: `flagged` rises from 121 to 135 and the
// total to 1169.
// CR-2026-027 gave this encoder the `default:` key beside a match's `cases`, so
// match-default-fields.yaml round-trips: `match` rises from 43 to 44 and the total
// to 1170.
// CR-2026-030 resolved a `name_from` template on encode, so name-from.yaml
// round-trips: `plain fixed` rises from 58 to 59 and the total to 1171.
// CR-2026-031's name_from var-mismatch fixture round-trips on every path, so
// `plain fixed` rises from 59 to 61 and the total to 1173.
// CR-2026-071: the encoder undoes the bare modifiers beside a transform, so both
// arithmetic-order-read.yaml vectors round-trip. log-of-non-positive's zero_is_absent
// stops round-tripping, deliberately: it did only because a `log10` stage was undone as
// the identity, and a stage that does not invert is now reported, as the reference
// reports it. `plain fixed` reads 94 and the total 1703, and 96 and 1705 with the two
// vectors guard-else-as-declared gained for the mutation gate.
// CR-2026-085/086's fixtures add four round trips, all `plain fixed` (96 -> 100):
// leftover-bytes' exact frame and range-before-lookup's three in-range vectors. (Its
// field was first named `flagged`, which this harness's text scan filed under that shape;
// it was renamed so the bucket reflects the fields.) The other
// two do not round-trip, by design: the leftover vector re-encodes without its leftover
// bytes (PS-474), and the omitted out-of-range reading has no value to write back.
// `match` was already reading 123 against a floor of 122. Total 1709.
const encodeFloorTotal = 2259 // +2: meta-semantic (CR-2026-179); +14: bitfield-string-padded-signed (CR-2026-170), plain fixed; +7: compute-pow (CR-2026-172); +9: byte-string-reread (CR-2026-175), plain fixed; +10: bytes-format-forms (all plain fixed; every vector round-trips); +13: match-fractional-range-keys (match; +22: minifloat-reference-binding (11 types x 2; +11: byte-group-member-var; +4: bitfield-string-field-endian (two fixtures), plain fixed; +4: expected-error (CR-2026-167); +1: repeat-sampled (CR-2026-156); +1: negative-zero (CR-2026-154), plain fixed; +4: wide-peek-selector, wide-peek-selector-u64 (match); +11: no-silent-empty - length-selects-layout (4) and literal-selects-branch (2), both match, tag-entry-constants (4) and tlv-case-field-list literal_only (1), both tlv; +3: match-discriminator-from-inner-case (CR-2026-151: a nested match fixes the discriminator), all match; +4: ascii-numeral (CR-2026-150); +6: bf16 (CR-2026-149; bf16-specials has no round trip); +12: the four tlv encode-gap fixtures (tlv-object-with-inner-branch, tlv-case-of-one-inline-branch, tlv-float-reading, tlv-scaled-wide-reading), all tlv, each vector failing before in all five (CR-2026-148 and the inline-branch claims); +2: tlv-name-object-and-scalar (a mapping is no value for a scalar case); +5: match-fractional-discriminator (match); -4 deliberately: PS-169 (CR-2026-146) rejects a label several codes share - vobo mutant_p32_01, mutant_p112_01, two_analog_xp_all, two_analog_xx_ain3 round-tripped only because the first code found was the right one; +4: mask-reference-binding (flagged); +17: CR-2026-141 (PS-551) derives an absent status peek from the reading that writes its bits, restoring the 27 milesight ct10x/ct30x/gs301/ws515 vectors below (gs301 h2s_v12_u16_word included), and rejects 10 whose supplied peek disagrees with those bits - vobo mutant_p32_03..07 and mutant_p112_03..07 (two lookup codes share one label, so the label writes the other code), all tlv; a skip is padding and contradicts no peek, so dnt status_empty_mask still round-trips; -27 deliberately: merging CR-2026-136 with CR-2026-134 - 27 tlv vectors of milesight ct10x/ct30x/gs301/ws515 round-tripped under CR-2026-136 by leaving a status peek's bits zero; the peek's lookup misses on an ordinary reading, so the decode omits it (PS-269), and PS-551 rejects the input that lacks it (Go also loses gs301 h2s_v12_u16_word, which its ordered API had recovered and the reference never did); +1: tlv-repeat-merge object_members_combine (CR-2026-110); +3: CR-2026-125 (tlv-repeat-merge number_keeps_collecting, tlv-repeat-collect-first-case, tlv-repeat-merge-shared-member); +2: CR-2026-128 (tlv-repeat-merge-shared-last, and tlv-repeat-object-collect now that each element finds its case); +3: CR-2026-134 (PS-551) derives what the input fixes: internal-discriminator kind_1/kind_2 (match) and flagged-group-field-list inner_groups (flagged); +3: byte-group-width (CR-2026-131); +4: ascii-latin1 (CR-2026-114); +4: lookup-reference-binding vectors added for the mutation gate; +6: CR-2026-109/132 lookup-reference-binding (its miss, plain-default, enum-default and unnamed-enum vectors and the tag-component vectors do not round-trip); +1: merging CR-2026-109 with CR-2026-134 - lookup-reference-binding an_unnamed_enum_code_is_omitted_but_bound reports no discriminator, now derived from its case (PS-551); +266: CR-2026-135/136 - a field list is laid out as a decoder reads it, so a peeked bit range or bool shares its byte with the next field, also across the end of a case body, object, flagged group or match body (vobo 150, dnt 75, milesight ct10x/ct30x/gs301/ws515 40, all tlv) and encoder-rounding (1, plain fixed); no vector lost; +6: CR-2026-121/123 fixtures (four new, five vectors) and repeat-element-null reserved_value, whose null now writes its sentinel (PS-544); +2: CR-2026-124 guard-set empty in/not_in; +4: match-u32-inline-discriminator (the peek fixture's internal word has no value to write back); +4: names-mangling-alike; +2: CR-2026-127 guard-boolean-operand; +2: CR-2026-118 guard-set (the other three fixtures are refused, PS-542 and PS-387); +6: registry divergence fixtures, all plain fixed (tag-component-ref does not round-trip); +5: CR-2026-111 fixtures (the two null vectors are refused, PS-529); +8: name-from-rendering (CR-2026-112); +2: boolean-literal (CR-2026-107); +13: the nine TS013-defect fixtures (tlv-tag-bit-range does not round-trip); +8: CR-2026-104 lookup label fixtures (plain fixed); +3: byte-group-bool; +3: tlv-case-field-list; +2: tlv-case-key-patterns round trips; +2: CR-2026-096 _meta fixtures that round-trip; +6: trimmed vendor vectors (am30x, ws50x, em310-tilt) now round-trip; +2: dl-atm41g2's two vendor vectors (+2); +36: vendor vectors (unit cleanup); +6: Milesight battery-channel vendor vectors (percent units)

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
	// 1300 -> 1298 deliberately (CR-2026-067, PS-434): two vobo vectors round-tripped only because their internal byte halves were all zero, which the encoder wrote for an internal field with no value. It now reports such a field instead.
	"tlv":         1607, // +11: byte-group-member-var; +4: expected-error (CR-2026-167); +5: no-silent-empty (tag-entry-constants 4, tlv-case-field-list literal_only); +12: the tlv encode-gap fixtures (see the total); +2: tlv-name-object-and-scalar (a mapping is no value for a scalar case); -4 deliberately: PS-169 (CR-2026-146), vobo mutant_p32_01, mutant_p112_01, two_analog_xp_all, two_analog_xx_ain3 (see the total); +17: CR-2026-141 (see the total); -27: CR-2026-136 with CR-2026-134 (see the total); +1: tlv-repeat-merge (CR-2026-110); +3: CR-2026-125 (tlv-repeat-merge number_keeps_collecting, tlv-repeat-collect-first-case, tlv-repeat-merge-shared-member); +2: CR-2026-128 (tlv-repeat-merge-shared-last, and tlv-repeat-object-collect now that each element finds its case); +265: CR-2026-136 layout (vobo 150, dnt 75, milesight 40); before that +3: tlv-case-field-list; +2: tlv-case-key-patterns; +6: trimmed vendor vectors (am30x, ws50x, em310-tilt) now round-trip
	"flagged":     198, // +4: mask-reference-binding; +1: CR-2026-134 flagged-group-field-list inner_groups (nested group and its mask derived)
	"plain fixed": 225, // +14: bitfield-string-padded-signed (CR-2026-170), plain fixed; +7: compute-pow (CR-2026-172); +9: byte-string-reread (CR-2026-175); +10: bytes-format-forms (all plain fixed; every vector round-trips); +22: minifloat-reference-binding (11 types x 2; plain fixed); +4: bitfield-string-field-endian (two fixtures); +1: negative-zero (CR-2026-154); +4: ascii-numeral (CR-2026-150); +6: bf16 (CR-2026-149); +4: ascii-latin1 (CR-2026-114); +1: CR-2026-135 encoder-rounding; +2: CR-2026-124 guard-set; +4: names-mangling-alike; +2: CR-2026-127 guard-boolean-operand; +2: CR-2026-118 guard-set; +6: registry divergence fixtures; +8: name-from-rendering (CR-2026-112); +8: CR-2026-104 lookup label fixtures; +9: TS13 fixtures; +2: CR-2026-96 _meta fixtures
	"match":       173, // +13: match-fractional-range-keys (match; every vector round-trips); +6: no-silent-empty (length-selects-layout 4, literal-selects-branch 2); +3: match-discriminator-from-inner-case (CR-2026-151); +4: wide-peek-selector, wide-peek-selector-u64 (match); the skipped-four vector does not round-trip; // +5: match-fractional-discriminator; +1: CR-2026-109 with CR-2026-134 (see the total); +5: lookup-reference-binding (CR-2026-109); +1: CR-2026-132 enum port; +2: CR-2026-134 internal-discriminator kind_1/kind_2 (discriminator derived from the case given); +4: match-u32-inline-discriminator; +2: boolean-literal (CR-2026-107)
	"byte_group":  28, // +3: byte-group-width (CR-2026-131); +3: byte-group-bool; +2: wide-group
	// 6 -> 5 with plain fixed 66 -> 67: a bucket move, not a regression. Composed
	// library schemas now carry only the definitions they reach, so one no longer
	// contains an unreferenced definition's `repeat` text for this harness to find.
	"repeat": 28, // +2: meta-semantic (CR-2026-179); +1: repeat-sampled (CR-2026-156); +5: CR-2026-111 fixtures; +2: TS013 fixtures; +6: CR-2026-121/123 fixtures and repeat-element-null reserved_value (PS-544)
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
	root := repoPath(t, "schemas", "devices")
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

// encodePlainFloorTotal is the number of corpus vectors that re-encode exactly through the
// *unordered* API - Decode then Encode, with no channel order carried between them.
//
// TestCorpusEncodeRoundTrip above uses DecodeOrdered/EncodeOrdered, so until CR-2026-029
// nothing measured the plain pair over the corpus at all. That let a real defect sit in it
// unseen: with several tlv cases claiming overlapping field names, the claiming pass ran in
// tag order and emitted both a case claiming one name and a case claiming that name and
// another, so em400-mud's `8367000000` came back as `036700008367000000`.
//
// It also let a comparison against the Python reference be made on the wrong path. The
// plain API documents a weaker contract - it assumes ascending tag order, which is how most
// devices in this corpus lay their channels out and not how ws515 and wt101 do - so its
// failures are not all defects, and this floor is lower than the ordered one on purpose.
// CR-2026-071 raised it to 1680, for the reasons given at encodeFloorTotal, and
// CR-2026-085/086's fixtures to 1686.
const encodePlainFloorTotal = 2231 // +2: meta-semantic (CR-2026-179); +14: bitfield-string-padded-signed (CR-2026-170), plain fixed; +7: compute-pow (CR-2026-172); +9: byte-string-reread (CR-2026-175); +10: bytes-format-forms (all plain fixed; every vector round-trips); +13: match-fractional-range-keys (match; +22: minifloat-reference-binding (11 types x 2; +11: byte-group-member-var; +4: bitfield-string-field-endian (two fixtures); +4: expected-error (CR-2026-167); +1: repeat-sampled (CR-2026-156); +1: negative-zero (CR-2026-154), plain fixed; +4: wide-peek-selector, wide-peek-selector-u64 (match); +10: no-silent-empty, the same fixtures less tag-entry-constants both_entries, whose tags are not ascending; +3: match-discriminator-from-inner-case (CR-2026-151: a nested match fixes the discriminator), all match; +4: ascii-numeral (CR-2026-150); +6: bf16 (CR-2026-149; bf16-specials has no round trip); +12: the four tlv encode-gap fixtures (tlv-object-with-inner-branch, tlv-case-of-one-inline-branch, tlv-float-reading, tlv-scaled-wide-reading), all tlv, each vector failing before in all five (CR-2026-148 and the inline-branch claims); +2: tlv-name-object-and-scalar (a mapping is no value for a scalar case); +5: match-fractional-discriminator (match); -4 deliberately: PS-169 (CR-2026-146) rejects a label several codes share - vobo mutant_p32_01, mutant_p112_01, two_analog_xp_all, two_analog_xx_ain3 round-tripped only because the first code found was the right one; +4: mask-reference-binding (flagged); +15: CR-2026-141, the same change on the plain path (25 restored, the same 10 rejected); -25 deliberately: CR-2026-136 with CR-2026-134, the same status-peek vectors as the ordered total (PS-551); +1: tlv-repeat-merge object_members_combine (CR-2026-110); +3: CR-2026-125 (tlv-repeat-merge number_keeps_collecting, tlv-repeat-collect-first-case, tlv-repeat-merge-shared-member); +2: CR-2026-128 (tlv-repeat-merge-shared-last, and tlv-repeat-object-collect now that each element finds its case); +3: CR-2026-134 (PS-551), the same three vectors as the ordered floor; +3: byte-group-width (CR-2026-131); +4: ascii-latin1 (CR-2026-114); +4: lookup-reference-binding vectors added for the mutation gate; +6: CR-2026-109/132 lookup-reference-binding (its miss, plain-default, enum-default and unnamed-enum vectors and the tag-component vectors do not round-trip); +1: the same vector as the ordered floor (CR-2026-109 with CR-2026-134); +262: CR-2026-135/136, the same layout change on the plain path (encoder-rounding included); +6: CR-2026-121/123 fixtures and repeat-element-null reserved_value (PS-544); +2: CR-2026-124 guard-set empty in/not_in; +4: match-u32-inline-discriminator (the peek fixture's internal word has no value to write back); +4: names-mangling-alike; +2: CR-2026-127 guard-boolean-operand; +2: CR-2026-118 guard-set; +6: registry divergence fixtures, all plain fixed (tag-component-ref does not round-trip); +5: CR-2026-111 fixtures; +8: name-from-rendering (CR-2026-112); +2: boolean-literal (CR-2026-107); +13: the nine TS013-defect fixtures (tlv-tag-bit-range does not round-trip); +8: CR-2026-104 lookup label fixtures; +3: byte-group-bool; +3: tlv-case-field-list; +2: tlv-case-key-patterns round trips; +2: CR-2026-096 _meta fixtures that round-trip; +6: trimmed vendor vectors (am30x, ws50x, em310-tilt) now round-trip; +2: dl-atm41g2's two vendor vectors (+2); +36: vendor vectors (unit cleanup); +6: Milesight battery-channel vendor vectors (percent units)

// TestCorpusEncodePlainRoundTrip measures the unordered pair, so the two contracts are
// ratcheted separately and neither can be mistaken for the other.
func TestCorpusEncodePlainRoundTrip(t *testing.T) {
	root := repoPath(t, "schemas", "devices")
	exact, total := 0, 0

	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(path, ".yaml") {
			return nil
		}
		raw, readErr := os.ReadFile(path)
		if readErr != nil {
			return nil
		}
		var doc encodeSchemaFile
		if yaml.Unmarshal(raw, &doc) != nil || len(doc.TestVectors) == 0 {
			return nil
		}
		parsed, parseErr := ParseSchema(string(raw))
		if parseErr != nil {
			return nil
		}
		for _, v := range doc.TestVectors {
			if v.Payload == "" {
				continue
			}
			payload, hexErr := hex.DecodeString(strings.ReplaceAll(v.Payload, " ", ""))
			if hexErr != nil {
				continue
			}
			total++
			fport := v.FPort
			if fport == nil {
				fport = v.FPortLower
			}
			var decoded map[string]any
			var decErr error
			if fport != nil {
				decoded, decErr = parsed.DecodeWithPort(payload, *fport)
			} else {
				decoded, decErr = parsed.Decode(payload)
			}
			if decErr != nil {
				continue
			}
			var out []byte
			var encErr error
			if fport != nil {
				out, encErr = parsed.EncodeWithPort(decoded, *fport)
			} else {
				out, encErr = parsed.Encode(decoded)
			}
			if encErr == nil && bytes.Equal(out, payload) {
				exact++
			}
		}
		return nil
	})
	if err != nil {
		t.Skipf("corpus unavailable: %v", err)
	}

	t.Log(fmt.Sprintf("plain (unordered) round-trips: %d of %d vectors decoded", exact, total))
	if exact < encodePlainFloorTotal {
		t.Errorf("only %d corpus vectors re-encode exactly through the unordered API, "+
			"floor is %d", exact, encodePlainFloorTotal)
	}
}
