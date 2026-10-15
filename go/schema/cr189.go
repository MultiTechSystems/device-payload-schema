package schema

// CR-2026-189 (PS-621 to PS-623): a repeat of single values whose elements are packed end
// to end in `packed_bits` bits, most significant bit first, from `bit_offset` bits into the
// byte at the read position. Mirrors packed_element_width, packed_repeat_error,
// packed_layout, the packed branch of _decode_repeat and _encode_packed_elements in
// tools/schema_interpreter.py.

import (
	"fmt"
	"regexp"
)

var packedElementType = regexp.MustCompile(`^(u|s|uint|int|i)(8|16|24|32|64)$`)

// packedElementWidth is packed_element_width: the width in bits of a packed element's
// integer type and whether it is signed; ok is false for any other type (PS-621).
func packedElementWidth(element map[string]any) (width int, signed bool, ok bool) {
	t, _ := element["type"].(string)
	m := packedElementType.FindStringSubmatch(t)
	if m == nil {
		return 0, false, false
	}
	fmt.Sscan(m[2], &width)
	return width, m[1] != "u" && m[1] != "uint", true
}

// packedRepeatError is packed_repeat_error: PS-621 over one field mapping, in the spec
// prototype's text, the first rule broken.
func packedRepeatError(field map[string]any) error {
	bitsRaw, hasBits := field["packed_bits"]
	offsetRaw, hasOffset := field["bit_offset"]
	if !hasBits && !hasOffset {
		return nil
	}
	name := "None"
	if s, ok := field["name"].(string); ok {
		name = s
	}
	where := fmt.Sprintf("Repeat '%s'", name)
	if field["type"] != "repeat" || !hasBits {
		hint := ""
		if field["type"] == "bool" {
			hint = "; did you mean `bit`?"
		}
		return fmt.Errorf("Field '%s': packed_bits and bit_offset belong to a repeat with `element` (PS-621)%s", name, hint)
	}
	bits, ok := strictInt(bitsRaw)
	if !ok || bits < 1 || bits > 32 {
		return fmt.Errorf("%s: packed_bits is an integer from 1 to 32 (PS-621)", where)
	}
	if hasOffset {
		offset, ok := strictInt(offsetRaw)
		if !ok || offset < 0 || offset > 7 {
			return fmt.Errorf("%s: bit_offset is an integer from 0 to 7 (PS-621)", where)
		}
	}
	width := 0
	if _, hasFields := field["fields"]; !hasFields {
		if element := asStringMap(field["element"]); element != nil {
			width, _, _ = packedElementWidth(element)
		}
	}
	if width < bits {
		return fmt.Errorf("%s: a packed element is one field of an integer type at least %d bits wide (PS-621)", where, bits)
	}
	_, hasCount := field["count"]
	_, hasLength := field["byte_length"]
	if !hasCount && !hasLength {
		return fmt.Errorf("%s: a packed repeat declares count or byte_length (PS-621)", where)
	}
	return nil
}

// strictInt reads a whole number written as an integer: never a bool, and not a float,
// as the prototype's isinstance(x, int) reads it.
func strictInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case float64:
		// A JSON-authored schema gives every number as a float.
		if n == float64(int(n)) {
			return int(n), true
		}
	}
	return 0, false
}

// packedSpan is packed_layout's span: the bytes `count` elements take from the bit offset,
// to the next byte boundary, or to the end of byteLength where that is further (PS-622).
func packedSpan(field Field, count, byteLength int) int {
	span := (field.BitOffset + count*field.PackedBits + 7) / 8
	if byteLength > span {
		span = byteLength
	}
	return span
}

// decodePacked reads a packed repeat's elements (PS-621, PS-622). Each element's bits are
// laid out as the element type's integer, big-endian, sign-extended where it is signed
// (PS-353), and decoded from those bytes by the element function, so its sentinel,
// modifiers and lookup apply as to any element.
func decodePacked(field Field, elements *repeatElements, ctx *DecodeContext, count, byteLength, maxIterations int) error {
	if count > maxIterations {
		return repeatLimitError(field, maxIterations, fmt.Sprintf("count %d", count), ctx.Offset, len(ctx.Data))
	}
	span := packedSpan(field, count, byteLength)
	if ctx.Offset+span > len(ctx.Data) {
		return nameUnderflow(underflowNeed(span, ctx.Offset, len(ctx.Data)), field.Name, "repeat")
	}
	stream := ctx.Data[ctx.Offset : ctx.Offset+span]
	savedData, savedOffset := ctx.Data, ctx.Offset
	defer func() { ctx.Data, ctx.Offset = savedData, savedOffset+span }()
	bits := field.PackedBits
	for i := 0; i < count; i++ {
		var raw int64
		for b := field.BitOffset + i*bits; b < field.BitOffset+(i+1)*bits; b++ {
			raw = raw<<1 | int64(stream[b>>3]>>(7-uint(b&7))&1)
		}
		if field.packedSigned && raw >= int64(1)<<(bits-1) {
			raw -= int64(1) << bits
		}
		width := field.packedWidth / 8
		element := make([]byte, width)
		for k := width - 1; k >= 0; k-- {
			element[k] = byte(raw)
			raw >>= 8
		}
		ctx.Data, ctx.Offset = element, 0
		if err := elements.element(); err != nil {
			return err
		}
	}
	return nil
}

// encodePacked is _encode_packed_elements (PS-623): each element, inverted through its
// modifiers as any element is, in `packed_bits` bits, MSB first, from `bit_offset`, the last
// byte padded with 0 bits. It returns the bytes and, per byte, the bits the repeat writes:
// none before `bit_offset`, so the bits the fields before it left pending are ORed in
// (PS-551, PS-622). A null is the element's first sentinel, raw (PS-544).
func encodePacked(field Field, records []any, ctx *EncodeContext) ([]byte, []byte, error) {
	bits := field.PackedBits
	var low, high int64
	if field.packedSigned {
		low, high = -(int64(1) << (bits - 1)), int64(1)<<(bits-1)-1
	} else {
		low, high = 0, int64(1)<<bits-1
	}
	stream := make([]bool, 0, len(records)*bits)
	for position, item := range records {
		record, ok := item.(map[string]any)
		if !ok {
			return nil, nil, fmt.Errorf("repeat field %q: expected each record to be a mapping", field.Name)
		}
		if field.Index != "" {
			bound := make(map[string]any, len(record)+1)
			for k, v := range record {
				bound[k] = v
			}
			bound[field.Index] = position + field.IndexBase
			record = bound
		}
		el := ctx.branch()
		el.Endian = "big"
		pushed := ctx.trace.pushNode(field.decl, "", "repeat", position)
		saved := ctx.newReportedObject()
		err := encodeFieldsSealed(field.Fields, record, el)
		ctx.enclosing = saved
		ctx.trace.pop(pushed)
		if err != nil {
			return nil, nil, err
		}
		el.flushPending()
		ctx.mergeWarnings(el)
		var number int64
		for i, b := range el.Buffer {
			if i == 0 && field.packedSigned && b >= 0x80 {
				number = -1
			}
			number = number<<8 | int64(b)
		}
		if number < low || number > high {
			return nil, nil, fmt.Errorf("Repeat '%s': element %d is %d, which does not fit in %d bits (PS-623)",
				field.Name, position, number, bits)
		}
		for b := bits - 1; b >= 0; b-- {
			stream = append(stream, (number>>uint(b))&1 == 1)
		}
	}
	byteLength := 0
	if n, ok := field.ByteLength.(int); ok {
		byteLength = n
	}
	span := packedSpan(field, len(records), byteLength)
	data := make([]byte, span)
	for i, set := range stream {
		if set {
			at := field.BitOffset + i
			data[at>>3] |= 1 << (7 - uint(at&7))
		}
	}
	covered := make([]byte, span)
	for i := range covered {
		covered[i] = 0xff
	}
	if span > 0 {
		covered[0] = 0xff >> uint(field.BitOffset)
	}
	return data, covered, nil
}

// writeCovered writes bytes of which only `covered` bits are the field's own, ORed over
// what is pending; a contradiction of a peek is held for the field list, as Write does.
func (ctx *EncodeContext) writeCovered(data, covered []byte) {
	if err := ctx.checkOverlay(data, covered); err != nil {
		if ctx.overlayErr == nil {
			ctx.overlayErr = err
		}
		return
	}
	ctx.cover(len(ctx.Buffer), covered)
	ctx.extendRaw(data, 0)
}

// encodePackedRepeat is the packed half of _encode_repeat and _encode_repeat_elements: a
// packed repeat's elements as encodePacked lays them out, padded to a byte_length the input
// gives as to a literal one (PS-623), written over the bits pending before them.
func encodePackedRepeat(field Field, records []any, data map[string]any, ctx *EncodeContext,
	siblings []Field, count int) error {
	ctx.ps564Depth++ // PS-564 is not checked inside a repeat (tail_unchecked)
	defer func() { ctx.ps564Depth-- }()
	wrapped := make([]any, len(records))
	for position, item := range records {
		if item == nil && (len(field.Fields) == 0 || len(field.Fields[0].Sentinel) == 0) {
			return fmt.Errorf("Repeat '%s' has null at position %d, and its element declares no `sentinel` to write for it (PS-544)",
				field.Name, position)
		}
		wrapped[position] = map[string]any{field.ElementName: item}
	}
	if count >= 0 && len(wrapped) != count {
		return fmt.Errorf("Repeat '%s' is given %d values for a count of %d (PS-551)",
			field.Name, len(wrapped), count)
	}
	bytes, covered, err := encodePacked(field, wrapped, ctx)
	if err != nil {
		return err
	}
	if n, ok := repeatSize(field.ByteLength, data, siblings); ok {
		for len(bytes) < n {
			bytes = append(bytes, 0)
			covered = append(covered, 0xff)
		}
		if len(bytes) != n {
			return fmt.Errorf("Repeat '%s' encodes to %d bytes for a byte_length of %d (PS-551)",
				field.Name, len(bytes), n)
		}
	}
	ctx.writeCovered(bytes, covered)
	return nil
}
