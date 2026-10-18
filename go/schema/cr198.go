package schema

// CR-2026-198 (PS-621 to PS-623 amended, PS-639, PS-640): a packed repeat may pack records
// of several fields, each declaring `packed_bits`; its `bit_offset` is any bit count from 0
// or a `$` reference to a field decoded before it; and with `consume: 0` it reads in place.
// Mirrors is_packed, packed_parts, packed_repeat_error and packed_offset_error in
// tools/schema_interpreter.py.

import (
	"fmt"
	"math"
	"strings"
)

// packedPart is one part of a packed element: its bits in the stream, and the width and
// signedness of the integer type it is decoded as.
type packedPart struct {
	bits   int
	width  int
	signed bool
}

// isPackedRepeat is is_packed: `packed_bits` on the repeat (PS-621), or on a field of its
// record (PS-639).
func isPackedRepeat(fm map[string]any) bool {
	if fm["type"] != "repeat" {
		return false
	}
	if _, ok := fm["packed_bits"]; ok {
		return true
	}
	members, ok := fm["fields"].([]any)
	if !ok {
		return false
	}
	for _, m := range members {
		if mm := asStringMap(m); mm != nil {
			if _, ok := mm["packed_bits"]; ok {
				return true
			}
		}
	}
	return false
}

// setPacked is packed_parts and the rest of a packed repeat's layout, read once at parse.
// The schema has been checked (packedRepeatError), so every part is well formed.
func setPacked(f *Field, fm map[string]any) {
	f.packedParts = nil
	if bits, ok := intValue(fm["packed_bits"]); ok {
		if element := asStringMap(fm["element"]); element != nil {
			width, signed, _ := packedElementWidth(element)
			f.packedParts = append(f.packedParts, packedPart{bits, width, signed})
			f.packedWidth, f.packedSigned = width, signed
		}
	} else if members, ok := fm["fields"].([]any); ok {
		for _, m := range members {
			mm := asStringMap(m)
			if mm == nil {
				continue
			}
			bits, _ := intValue(mm["packed_bits"])
			width, signed, _ := packedElementWidth(mm)
			f.packedParts = append(f.packedParts, packedPart{bits, width, signed})
		}
	}
	f.PackedBits = 0
	for _, p := range f.packedParts {
		f.PackedBits += p.bits
	}
	if ref, ok := fm["bit_offset"].(string); ok {
		f.bitOffsetRef = ref
		f.BitOffset = 0
	} else {
		f.BitOffset, _ = intValue(fm["bit_offset"])
	}
	if c, ok := fm["consume"]; ok {
		if n, ok := toFloat64(c); ok && n == 0 {
			f.packedInPlace = true
		}
	}
	for i := range f.Fields {
		f.Fields[i].Endian = "big"
	}
}

// packedRepeatError is packed_repeat_error: PS-621, PS-639 and PS-640 over one field
// mapping, in the spec prototype's text, the first rule broken. A field of a packed record
// (inRecord) declares `packed_bits` as a part, not as a repeat.
func packedRepeatError(field map[string]any, inRecord bool) error {
	_, hasBits := field["packed_bits"]
	_, hasOffset := field["bit_offset"]
	packed := isPackedRepeat(field)
	if !((hasBits && !inRecord) || hasOffset || packed) {
		return nil
	}
	name := "None"
	if s, ok := field["name"].(string); ok {
		name = s
	}
	where := fmt.Sprintf("Repeat '%s'", name)
	if field["type"] != "repeat" || !packed {
		hint := ""
		if field["type"] == "bool" {
			hint = "; did you mean `bit`?"
		}
		return fmt.Errorf("Field '%s': packed_bits and bit_offset belong to a packed repeat (PS-621, PS-639)%s", name, hint)
	}
	_, hasFields := field["fields"]
	if hasBits && hasFields {
		return fmt.Errorf("%s: a packed record declares packed_bits on each field, not on the repeat (PS-639)", where)
	}
	if hasOffset {
		valid := false
		switch v := field["bit_offset"].(type) {
		case string:
			valid = strings.HasPrefix(v, "$") && len(v) > 1
		default:
			if n, ok := strictInt(v); ok && n >= 0 {
				valid = true
			}
		}
		if !valid {
			return fmt.Errorf("%s: bit_offset is a bit count, 0 or more, or a $ reference (PS-621)", where)
		}
	}
	if c, ok := field["consume"]; ok {
		bad := false
		if b, isBool := c.(bool); isBool {
			bad = b // Python's True != 0, False == 0
		} else if n, isNum := toFloat64(c); !isNum || n != 0 {
			bad = true
		}
		if bad {
			return fmt.Errorf("%s: a packed repeat declares consume: 0 or no consume (PS-640)", where)
		}
	}
	type part struct {
		bits    any
		element map[string]any
	}
	var parts []part
	if hasBits {
		parts = []part{{field["packed_bits"], asStringMap(field["element"])}}
	} else {
		members, _ := field["fields"].([]any)
		for _, m := range members {
			mm := asStringMap(m)
			if mm == nil {
				return fmt.Errorf("%s: every field of a packed record declares packed_bits (PS-639)", where)
			}
			if _, ok := mm["packed_bits"]; !ok {
				return fmt.Errorf("%s: every field of a packed record declares packed_bits (PS-639)", where)
			}
			parts = append(parts, part{mm["packed_bits"], mm})
		}
	}
	for _, p := range parts {
		bits, ok := strictInt(p.bits)
		if !ok || bits < 1 || bits > 32 {
			return fmt.Errorf("%s: packed_bits is an integer from 1 to 32 (PS-621)", where)
		}
		width := 0
		if p.element != nil {
			width, _, _ = packedElementWidth(p.element)
		}
		if width < bits {
			return fmt.Errorf("%s: a packed part is an integer type at least %d bits wide (PS-621, PS-639)", where, bits)
		}
	}
	_, hasCount := field["count"]
	_, hasLength := field["byte_length"]
	if !hasCount && !hasLength {
		return fmt.Errorf("%s: a packed repeat declares count or byte_length (PS-621)", where)
	}
	return nil
}

// packedOffsetValue checks a referenced bit_offset's value: a whole number from 0, else
// packed_offset_error's text, the value as JavaScript's String(n) writes it.
func packedOffsetValue(field Field, value any) (int, error) {
	if _, isBool := value.(bool); !isBool {
		if n, ok := toFloat64(value); ok && n == math.Trunc(n) && n >= 0 {
			return int(n), nil
		}
	}
	shown := ""
	if _, isBool := value.(bool); isBool {
		shown = pyRepr(value)
	} else if n, ok := toFloat64(value); ok {
		shown = formatLookupValue(n)
	} else {
		shown = pyRepr(value)
	}
	return 0, fmt.Errorf("Repeat '%s': bit_offset %s is %s, not a bit count (PS-621)", field.Name, field.bitOffsetRef, shown)
}

// packedDecodeOffset is the bit offset a packed repeat decodes from: literal, or the value
// of the field its reference names (PS-621).
func packedDecodeOffset(field Field, ctx *DecodeContext) (int, error) {
	if field.bitOffsetRef == "" {
		return field.BitOffset, nil
	}
	name := strings.TrimPrefix(field.bitOffsetRef, "$")
	value, ok := ctx.Variables[name]
	if !ok || value == nil {
		return 0, fmt.Errorf("Repeat '%s': bit_offset variable not found: %s", field.Name, name)
	}
	return packedOffsetValue(field, value)
}

// packedEncodeOffset is the bit offset a packed repeat encodes from: literal, or the value
// the input gives the field its reference names. A computed offset is not in the input, so
// such a repeat decodes but is not encoded (PS-623).
func packedEncodeOffset(field Field, data map[string]any) (int, error) {
	if field.bitOffsetRef == "" {
		return field.BitOffset, nil
	}
	value, ok := data[strings.TrimPrefix(field.bitOffsetRef, "$")]
	if !ok || value == nil {
		return 0, fmt.Errorf("Repeat '%s': its bit_offset %s is not in the input; a repeat whose offset is computed decodes but is not encoded (PS-623)",
			field.Name, field.bitOffsetRef)
	}
	return packedOffsetValue(field, value)
}

// packedCovered is packed_covered: the bits a packed repeat writes, per byte - every bit
// from the offset on; the bits before it are the fields before the repeat's.
func packedCovered(offset, span int) []byte {
	covered := make([]byte, span)
	for i := range covered {
		switch {
		case i < offset/8:
			covered[i] = 0
		case i == offset/8:
			covered[i] = 0xff >> uint(offset%8)
		default:
			covered[i] = 0xff
		}
	}
	return covered
}
