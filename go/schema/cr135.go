package schema

import (
	"fmt"
	"math"
	"strconv"
	"strings"
)

// CR-2026-135 (PS-552, PS-168): an encoder rounds the value it computes for an integer
// field - a bit range's included - to the nearest integer, a tie to even, on the binary64
// value the arithmetic produced, and rejects a value that rounds outside the field's type.
// Mirrors round_to_integer, integer_bounds and check_fits in tools/schema_interpreter.py.
//
// Go rounded a bit range half up (math.Round) and wrote a whole integer that did not fit
// by wrapping it - u8 256 as 00, s8 -128.6 as 7f - with no error.

// roundToInteger is PS-552's rounding: the nearest integer, a tie to the even one.
func roundToInteger(v float64) float64 {
	return math.RoundToEven(v)
}

// integerFitError is the PS-168 rejection, in the spec prototype's words, or nil where the
// rounded value fits `bits` bits, signed or not. `raw` is the rounded value, `given` the
// value the input gave, written as JavaScript's String(number) writes it.
func integerFitError(raw float64, bits int, signed bool, name string, given any) error {
	return fitError(raw, bits, signed, name, given, "")
}

// fitError is integerFitError with the text a bit range adds after its range: " for
// u8[0:3]", the range's type (PS-064, CR-2026-136), as the spec prototype writes it.
func fitError(raw float64, bits int, signed bool, name string, given any, suffix string) error {
	low, high, lowText, highText := integerBounds(bits, signed)
	inRange := raw >= low && raw <= high
	if bits == 64 {
		inRange = raw >= low && raw < high // high is one past the top here
	}
	if inRange {
		return nil
	}
	return fmt.Errorf("Field '%s': %s encodes as %s, outside %s to %s%s (PS-168)",
		name, givenText(given), strconv.FormatFloat(raw, 'f', -1, 64), lowText, highText, suffix)
}

// integerBounds is the range `bits` bits hold, as floats for the comparison and as exact
// text for the message. At 64 bits the float `high` is 2^64 or 2^63 itself, one past the
// top, so the caller compares it exclusively.
func integerBounds(bits int, signed bool) (low, high float64, lowText, highText string) {
	if signed {
		if bits == 64 {
			return -9223372036854775808.0, 9223372036854775808.0,
				"-9223372036854775808", "9223372036854775807"
		}
		h := int64(1)<<(bits-1) - 1
		return float64(-h - 1), float64(h), strconv.FormatInt(-h-1, 10), strconv.FormatInt(h, 10)
	}
	if bits == 64 {
		return 0, 18446744073709551616.0, "0", "18446744073709551615"
	}
	h := uint64(1)<<bits - 1
	return 0, float64(h), "0", strconv.FormatUint(h, 10)
}

// bitRangeFitError is the same check for a bit range, whose type is its width (PS-552,
// PS-064): a value the range cannot hold is rejected, never masked into a neighbour's
// bits. Kept apart so the reading of a range's type can be adjusted in one place.
func bitRangeFitError(raw float64, width int, rangeType FieldType, name string, given any) error {
	return fitError(raw, width, strings.HasPrefix(string(rangeType), "s"), name, given,
		" for "+string(rangeType))
}

// CR-2026-136 (PS-064, PS-062): a field list is laid out as a decoder reads it. A bit
// range or a bool that does not `consume` leaves the bytes it filled pending - a decoder
// peeks them, and the next field reads them again - so the next field's bytes are ORed
// into them rather than written after them. Mirrors FrameBuffer in
// tools/schema_interpreter.py. The run used to be written whole and the next field's
// bytes after it: two bytes where the decoder reads one (r718x, vobo, dnt).

// placeBits ORs `bits` at `start` into the pending base of `baseSize` bytes, read and
// written in `endian`, then moves `consume` bytes from pending to the buffer (PS-016).
func (ctx *EncodeContext) placeBits(baseSize int, endian string, start uint, bits uint64, consume int) {
	ctx.placeNamedBits(baseSize, endian, start, bits, consume, 0, "", false)
}

// placeNamedBits is placeBits recording a named range of `width` bits for the check where
// the frame closes (CR-2026-141, PS-551). A derived one - absent from the input and not
// consuming - writes no bits: the fields that write them fix its value.
func (ctx *EncodeContext) placeNamedBits(baseSize int, endian string, start uint, bits uint64,
	consume int, width uint, name string, derived bool) {
	if baseSize < 1 {
		baseSize = 1
	}
	if width > 0 && name != "" {
		peek := peekRecord{pos: len(ctx.Buffer), size: baseSize, endian: endian, start: start,
			width: width, raw: bits, derived: derived, name: name}
		ctx.peeks = append(ctx.peeks, peek)
		if !derived {
			ctx.cover(len(ctx.Buffer), peek.maskBytes())
		}
	}
	for len(ctx.pending) < baseSize {
		ctx.pending = append(ctx.pending, 0)
	}
	base := decodeUint(ctx.pending[:baseSize], endian) | bits<<start
	copy(ctx.pending[:baseSize], encodeUint(base, baseSize, endian))
	if consume > 0 {
		for len(ctx.pending) < consume {
			ctx.pending = append(ctx.pending, 0)
		}
		ctx.Buffer = append(ctx.Buffer, ctx.pending[:consume]...)
		ctx.pending = append([]byte(nil), ctx.pending[consume:]...)
	}
}

// extend is FrameBuffer.extend: `data` is ORed over the pending bytes from its start, and
// its last `carried` bytes - a nested list's unconsumed tail - stay pending, ORed with any
// older pending bytes beyond them, rather than being written.
func (ctx *EncodeContext) extend(data []byte, carried int) error {
	full := make([]byte, len(data))
	for i := range full {
		full[i] = 0xff
	}
	if err := ctx.checkOverlay(data, full); err != nil {
		return err
	}
	ctx.cover(len(ctx.Buffer), full)
	ctx.extendRaw(data, carried)
	return nil
}

// checkOverlay is the PS-551 check before an OR (CR-2026-141): a field whose written bits
// (`covered`) are clear where a peek before it set them would change both values.
func (ctx *EncodeContext) checkOverlay(data, covered []byte) error {
	shared := len(ctx.pending)
	if len(data) < shared {
		shared = len(data)
	}
	for i := 0; i < shared; i++ {
		if ctx.pending[i]&^data[i]&covered[i] != 0 {
			frame := append(append([]byte(nil), ctx.Buffer...), data...)
			return fmt.Errorf("%s", peekDisagreement(ctx.peeks, frame))
		}
	}
	return nil
}

// writePadding writes a skip's zeros (CR-2026-141, the reference's padding()). Padding
// carries no value, so its bits fix no peek and contradict none: a decoder skips them,
// whatever the peek before set there. They cover nothing and are not checked.
func (ctx *EncodeContext) writePadding(length int) {
	ctx.extendRaw(make([]byte, length), 0)
}

// extendRaw ORs and writes `data` with no coverage bookkeeping (see extend).
func (ctx *EncodeContext) extendRaw(data []byte, carried int) {
	if carried > len(data) {
		carried = len(data)
	}
	if len(ctx.pending) > 0 && len(data) > 0 {
		shared := len(ctx.pending)
		if len(data) < shared {
			shared = len(data)
		}
		merged := append([]byte(nil), data...)
		for i := 0; i < shared; i++ {
			merged[i] |= ctx.pending[i]
		}
		ctx.pending = append([]byte(nil), ctx.pending[shared:]...)
		data = merged
	}
	if carried > 0 {
		ctx.Buffer = append(ctx.Buffer, data[:len(data)-carried]...)
		tail := append([]byte(nil), data[len(data)-carried:]...)
		for i, b := range ctx.pending {
			if i < len(tail) {
				tail[i] |= b
			} else {
				tail = append(tail, b)
			}
		}
		ctx.pending = tail
		return
	}
	ctx.Buffer = append(ctx.Buffer, data...)
}

// flushPending writes the bytes still pending: a decoder reads them.
func (ctx *EncodeContext) flushPending() {
	ctx.Buffer = append(ctx.Buffer, ctx.pending...)
	ctx.pending = nil
}

// beginFieldList starts a field list with nothing pending of its own; endFieldList hands
// the list's bytes to the enclosing list as FrameBuffer.extend(Frame) does - ORed over its
// pending bytes, with the list's own pending tail carried where `carry` is set and
// written otherwise.
//
// The mark also holds the enclosing list's coverage of the bytes from `start` - its
// pending bytes' (CR-2026-141) - set aside so the nested list's own bits are what the
// check before the OR reads, as the reference's nested Frame carries its own.
type fieldListMark struct {
	outer    []byte
	start    int
	outerCov []byte
}

func (ctx *EncodeContext) beginFieldList() fieldListMark {
	mark := fieldListMark{outer: ctx.pending, start: len(ctx.Buffer)}
	ctx.pending = nil
	if len(ctx.covered) > mark.start {
		mark.outerCov = append([]byte(nil), ctx.covered[mark.start:]...)
		ctx.covered = ctx.covered[:mark.start]
	}
	return mark
}

func (ctx *EncodeContext) endFieldList(mark fieldListMark, carry bool) error {
	outer, start := mark.outer, mark.start
	nested := append(append([]byte(nil), ctx.Buffer[start:]...), ctx.pending...)
	carried := 0
	if carry {
		carried = len(ctx.pending)
	}
	ctx.Buffer = ctx.Buffer[:start]
	ctx.pending = outer
	// The nested list's bits are already recorded at these positions (CR-2026-141).
	covered := make([]byte, len(nested))
	for i := range covered {
		covered[i] = ctx.coveredAt(start + i)
	}
	if err := ctx.checkOverlay(nested, covered); err != nil {
		return err
	}
	ctx.cover(start, mark.outerCov) // the enclosing list's bits rejoin the nested list's
	ctx.extendRaw(nested, carried)
	return nil
}

// --- CR-2026-141 (PS-551): a peek whose bits other fields write is derived ---------------
//
// Mirrors Frame/FrameBuffer's `covered` and `peeks` and check_peeks in
// tools/schema_interpreter.py. Positions are the context's absolute ones: a nested field
// list writes into the same Buffer at the same offsets, so nothing is shifted.

// peekRecord is a named bit range or bool placed: `raw` is its unsigned bits as given,
// unused where it was derived.
type peekRecord struct {
	pos, size  int
	endian     string
	start      uint
	width      uint
	raw        uint64
	derived    bool
	name       string
}

func (p peekRecord) maskBits() uint64 {
	if p.width >= 64 {
		return ^uint64(0)
	}
	return (uint64(1)<<p.width - 1) << p.start
}

func (p peekRecord) maskBytes() []byte {
	return encodeUint(p.maskBits(), p.size, p.endian)
}

// found is the range's unsigned bits as `frame` holds them, zero past its end.
func (p peekRecord) found(frame []byte) uint64 {
	window := make([]byte, p.size)
	for i := range window {
		if p.pos+i < len(frame) {
			window[i] = frame[p.pos+i]
		}
	}
	return (decodeUint(window, p.endian) & p.maskBits()) >> p.start
}

func (ctx *EncodeContext) cover(position int, mask []byte) {
	for len(ctx.covered) < position+len(mask) {
		ctx.covered = append(ctx.covered, 0)
	}
	for i, m := range mask {
		ctx.covered[position+i] |= m
	}
}

func (ctx *EncodeContext) coveredAt(position int) byte {
	if position < len(ctx.covered) {
		return ctx.covered[position]
	}
	return 0
}

// peekDisagreement is peek_disagreement: the first supplied peek `frame` contradicts.
func peekDisagreement(peeks []peekRecord, frame []byte) string {
	for _, p := range peeks {
		if p.derived || p.pos+p.size > len(frame) {
			continue
		}
		if found := p.found(frame); found != p.raw {
			return fmt.Sprintf("Field '%s' is given %d, but the fields that share its bits write %d (PS-551)",
				p.name, p.raw, found)
		}
	}
	return "A field writes bits a peek before it set otherwise (PS-551)"
}

// checkPeeks is check_peeks over the frame written so far (Buffer, then pending), for the
// peeks recorded from index `from`: each reads back what it was given, and a derived one
// has every bit written by another field, or it is missing.
func (ctx *EncodeContext) checkPeeks(from int) error {
	frame := append(append([]byte(nil), ctx.Buffer...), ctx.pending...)
	for _, p := range ctx.peeks[from:] {
		if p.derived {
			mask := p.maskBytes()
			for i, m := range mask {
				if ctx.coveredAt(p.pos+i)&m != m {
					return missingFieldError(p.name)
				}
			}
			continue
		}
		if p.found(frame) != p.raw {
			return fmt.Errorf("%s", peekDisagreement([]peekRecord{p}, frame))
		}
	}
	return nil
}

// sealFrom closes what was written from `start` and recorded from peek `from`: its bytes
// are now whole, as the reference's plain bytes are, and its peeks are checked and gone.
func (ctx *EncodeContext) sealFrom(start, from int) {
	end := len(ctx.Buffer) + len(ctx.pending)
	full := make([]byte, end-start)
	for i := range full {
		full[i] = 0xff
	}
	ctx.cover(start, full)
	ctx.peeks = ctx.peeks[:from]
}

// isPeekField is is_peek_field: a named bit range or bool that does not consume (PS-060,
// PS-066).
func isPeekField(f Field) bool {
	if f.Name == "" || f.Consume != 0 {
		return false
	}
	return f.Type == TypeBool || f.Type == TypeBoolLower ||
		(f.Type != TypeBitfieldString && isBareBitfieldField(f))
}

// coveredPeeks is covered_peeks: the indices of the peeks in `fields` a whole field after
// them writes over - read from the schema, so a tlv case can be chosen without them
// (PS-551, CR-2026-141). A peek counts only where the next field that reads bytes is a
// plain whole field: a range that consumes, a skip or a construct ends its span first,
// and its bits may be left unwritten (wt303's status nibble beside a mode nibble).
// Whether a peek is derived when encoding is decided by the bits actually written.
func coveredPeeks(fields []Field) map[int]bool {
	out := map[int]bool{}
	var pending []int
	for i, f := range fields {
		if isPeekField(f) {
			pending = append(pending, i)
			continue
		}
		if isComputedField(f) || isLiteralField(f) {
			continue
		}
		t := string(f.Type)
		construct := f.MatchInline != nil || f.TLVInline != nil || len(f.ByteGroup) > 0 ||
			f.Flagged != nil || f.Ref2 != "" || f.Type == TypeMatch || f.Type == TypeMatchLower ||
			f.Type == TypeTLV || f.Type == TypeTLVLower
		bits := strings.ContainsAny(t, "[:<")
		switch {
		case construct, bits, f.Type == TypeSkip, f.Type == TypeSkipLower,
			f.Type == TypeObject, f.Type == TypeObjectLower, f.Type == TypeRepeat,
			f.Type == TypeRepeatLower, f.Type == TypeBool, f.Type == TypeBoolLower:
		default:
			for _, p := range pending {
				out[p] = true
			}
		}
		pending = nil
	}
	return out
}

// givenText writes the value the input gave as JavaScript's String(number) would, so a
// cross-language diff compares equal strings: an integer exactly, a float as
// formatLookupValue writes it, anything else as %v.
func givenText(given any) string {
	switch v := given.(type) {
	case int:
		return strconv.FormatInt(int64(v), 10)
	case int8:
		return strconv.FormatInt(int64(v), 10)
	case int16:
		return strconv.FormatInt(int64(v), 10)
	case int32:
		return strconv.FormatInt(int64(v), 10)
	case int64:
		return strconv.FormatInt(v, 10)
	case uint:
		return strconv.FormatUint(uint64(v), 10)
	case uint8:
		return strconv.FormatUint(uint64(v), 10)
	case uint16:
		return strconv.FormatUint(uint64(v), 10)
	case uint32:
		return strconv.FormatUint(uint64(v), 10)
	case uint64:
		return strconv.FormatUint(v, 10)
	case float32:
		return formatLookupValue(float64(v))
	case float64:
		return formatLookupValue(v)
	}
	return fmt.Sprintf("%v", given)
}

// exactInteger is the value as an exact integer where the input gave one and nothing
// between the input and the bytes changes it. A u64 past 2^53 is then written as given,
// where the float conversion wrote 18446744073709551615 as 8000000000000000.
func exactInteger(given any, field Field) (int64, uint64, bool, bool) {
	if len(field.Transform) > 0 || len(field.Modifiers) > 0 || field.Mult != nil ||
		field.Div != nil || field.Add != nil || len(field.Lookup) > 0 || len(field.LookupArray) > 0 {
		return 0, 0, false, false
	}
	switch v := given.(type) {
	case int:
		return int64(v), 0, false, true
	case int64:
		return v, 0, false, true
	case uint64:
		return 0, v, true, true
	}
	return 0, 0, false, false
}
