package schema

import "fmt"

// CR-2026-085 (bytes after the last field; a tlv tag cut off at the end) and
// CR-2026-086 (valid_range after the arithmetic and before the lookup).

// rangeStash records, for the field being decoded, what valid_range compares (PS-475).
// owner is the field's name, so a stash left by some other field - a member of a nested
// construct decoded inside it - is never read as this field's.
type rangeStash struct {
	owner        string
	preLookup    any
	hasPreLookup bool
	guardElse    bool
}

// rangeValue is _range_value: whether valid_range compares anything for this field, and
// the value it compares. A looked-up field is compared on the number its label stands
// for - compared after the lookup, a label was never a number and every one read
// "good". A failed guard's `else` ends the sequence (PS-443) and is not compared at all.
func (ctx *DecodeContext) rangeValue(field Field, value any) (bool, any) {
	st := ctx.rangeStash
	if st.owner != field.Name {
		st = rangeStash{}
	}
	if st.guardElse {
		return false, value
	}
	if field.Lookup != nil || field.LookupArray != nil {
		return st.hasPreLookup, st.preLookup
	}
	return true, value
}

// rangeOmitsDecoded is rangeOmits on the value rangeValue says is compared.
func (ctx *DecodeContext) rangeOmitsDecoded(field Field, value any) bool {
	checked, v := ctx.rangeValue(field, value)
	return checked && rangeOmits(field, v)
}

// reportLeftover is _report_leftover (PS-472): bytes after the last field of the
// selected list are reported, not dropped in silence. The decode is reported as it would
// be otherwise; this only warns, with the offset of the first byte not decoded and the
// count from there to the end. A frame from newer firmware and a frame decoded with the
// wrong layout both used to look complete. Where a PS-302 warning already said what was
// left, that warning is this one.
func (ctx *DecodeContext) reportLeftover() {
	left := len(ctx.Data) - ctx.Offset
	if left <= 0 || ctx.leftoverReported {
		return
	}
	ctx.Warnings = append(ctx.Warnings, fmt.Sprintf(
		"%d byte(s) after the last field left undecoded, from offset %d (PS-472)",
		left, ctx.Offset))
}

// tlvTagWidth is the bytes a tlv entry's tag occupies: tag_size, or the summed widths of
// its tag_fields, read as the loop reads them.
func tlvTagWidth(field Field, tagSize int) int {
	if len(field.TagFields) == 0 {
		return tagSize
	}
	width := 0
	for _, tf := range field.TagFields {
		width += tagFieldWidth(tf)
	}
	return width
}

// tagFieldWidth is the bytes one tag component takes: its `length` where declared, else
// its type's width. Decoding read `length` defaulting to 1 while encoding took the type's
// width, so a `u16` component was read as one byte and written as two; every other
// implementation reads it by its type.
func tagFieldWidth(tf Field) int {
	if tf.Length > 0 {
		return tf.Length
	}
	if _, hi, known := integerRange(tf.Type); known {
		switch {
		case hi > 0xFFFFFF:
			return 4
		case hi > 0xFFFF:
			return 3
		case hi > 0xFF:
			return 2
		}
	}
	return 1
}
