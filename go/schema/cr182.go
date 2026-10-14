package schema

// CR-2026-182 (PS-453 amended, PS-603, PS-604): a whole numeric field - an integer, a
// float, a minifloat, `udec`/`sdec` or a word-ordered variant - declaring `consume: 0`
// reads its value in place and leaves the read position, so the fields after it read the
// same bytes again. An encoder writes nothing for it and does not require it: the bytes
// fix it. A value given for it is compared with what the bytes the other fields write over
// its span decode to, through its own type, arithmetic and lookup (CR-2026-175's check,
// citing PS-604). Mirrors is_peeked_number and FrameBuffer.echo in the reference.

// wholeNumberSize is whole_number_size: the byte width of a whole numeric type, 0 for any
// other. Types are canonical here (parseFieldMap resolves the aliases).
func wholeNumberSize(t FieldType) int {
	switch string(t) {
	case "u8", "s8", "udec", "sdec":
		return 1
	case "u16", "s16", "f16", "bf16", "uflt16", "sflt16":
		return 2
	case "u24", "s24", "sflt24":
		return 3
	case "u32", "s32", "f32", "u32le16", "s32le16", "u32be16le", "s32be16le", "f32le16", "f32be16le":
		return 4
	case "u64", "s64", "f64":
		return 8
	}
	return 0
}

// isPeekedNumber is is_peeked_number: a whole numeric field declaring `consume: 0`.
func isPeekedNumber(f Field) bool {
	return f.ConsumeDeclared && f.Consume == 0 && wholeNumberSize(f.Type) > 0
}

// decodeInPlace reads a whole number that does not consume and puts the position back
// (PS-603).
func decodeInPlace(field Field, ctx *DecodeContext) (any, error) {
	start := ctx.Offset
	inner := field
	inner.ConsumeDeclared = false
	inner.Consume = 0
	value, err := decodeField(inner, ctx)
	ctx.Offset = start
	return value, err
}

// peekedNumberValue is what a whole number decodes from `span` (PS-604): false where it
// has no value (a sentinel, a lookup miss, a span that does not decode).
func peekedNumberValue(field Field, span []byte, endian string) (any, bool) {
	inner := field
	inner.ConsumeDeclared = false
	dctx := NewDecodeContext(span, endian)
	value, err := decodeField(inner, dctx)
	if err != nil || value == nil || value == omitted || value == absentSentinel {
		return nil, false
	}
	if f, ok := value.(float64); ok && f == 0 {
		value = 0.0 // PS-566
	}
	return value, true
}
