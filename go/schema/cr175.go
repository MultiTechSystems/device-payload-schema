package schema

// CR-2026-175 (PS-453 and PS-015 amended, PS-588, PS-589): a `bytes`, `hex` or `ascii`
// field declaring `consume: 0` reads and reports its span and leaves the read position, so
// the fields after it read the same bytes again. An encoder writes nothing for it and does
// not require it; a value given for it is compared with what the field decodes from the
// bytes the other fields write over its span, and rejected where they differ.

import (
	"encoding/hex"
	"fmt"
	"reflect"
	"strings"
)

// echoRecord is one non-consuming byte string given a value: where a decoder would read
// it in the frame, the field, the value given, and its length (-1 for `remaining`, which
// reaches the end of the frame it is checked in).
type echoRecord struct {
	pos    int
	field  Field
	given  any
	length int
	endian string // the encode's byte order, for a whole number (CR-2026-182)
}

// isEcho is the reference's is_echo: a byte string declaring `consume: 0` (PS-588), or a
// whole numeric one (PS-603, CR-2026-182).
func isEcho(f Field) bool {
	switch f.Type {
	case TypeBytes, TypeBytesLower, TypeHex, TypeHexLower, TypeAscii, TypeAsciiLower:
		return f.ConsumeDeclared && f.Consume == 0
	}
	return isPeekedNumber(f)
}

// recordEcho queues a given value for the check where the frame closes. The decoder reads
// it at the start of any bytes still pending, which is the end of Buffer.
func (ctx *EncodeContext) recordEcho(field Field, data map[string]any) error {
	given, ok := data[field.Name]
	if !ok || given == nil {
		return nil
	}
	length := field.Length
	switch {
	case isPeekedNumber(field):
		length = wholeNumberSize(field.Type) // PS-604
	case field.LengthRef != "":
		n, ok := toInt(data[field.LengthRef])
		if !ok || n < 0 {
			return missingFieldError(field.LengthRef)
		}
		length = n
	case length < 0:
		length = -1
	case length == 0 && !declaresZeroLength(field):
		length = 1
	}
	ctx.echoes = append(ctx.echoes, echoRecord{len(ctx.Buffer), field, given, length, ctx.Endian})
	return nil
}

// echoValue is what the field decodes from `span`: its format and separator, or PS-534's
// trailing 0x00 removal, applied as a decoder applies them.
func echoValue(field Field, span []byte) (any, bool) {
	switch field.Type {
	case TypeBytes, TypeBytesLower:
		return formatBytes(span, field.Format, field.Separator), true
	case TypeHex, TypeHexLower:
		return hex.EncodeToString(span), true
	}
	runes := make([]rune, len(span))
	for i, b := range span {
		runes[i] = rune(b)
	}
	text := strings.TrimRight(string(runes), "\x00")
	if field.Charset == "utf-8" { // PS-624
		utf, err := decodeUTF8Text(field.Name, span, "trailing")
		if err != nil {
			return nil, false
		}
		text = utf
	}
	text = trimText(field, text) // PS-641
	if field.Numeral != "" {
		n, err := decodeNumeral(field.Name, text, field.Numeral)
		if err != nil {
			return nil, false
		}
		return n, true
	}
	return text, true
}

// echoNormal brings a value to one representation for comparison: numbers as float64,
// lists element by element.
func echoNormal(v any) any {
	switch x := v.(type) {
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = echoNormal(e)
		}
		return out
	case []byte:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = float64(e)
		}
		return out
	case string, bool:
		return x
	}
	if f, ok := toFloat64(v); ok {
		return f
	}
	return v
}

// echoText writes a value as a PS-589 message does: text in single quotes, a number as
// JavaScript's String(number), a list of octets as '[1, 2]'.
func echoText(v any) string {
	switch x := v.(type) {
	case string:
		return "'" + x + "'"
	case bool:
		if x {
			return "true"
		}
		return "false"
	case []any:
		parts := make([]string, len(x))
		for i, e := range x {
			parts[i] = strings.Trim(echoText(e), "'")
		}
		return "'[" + strings.Join(parts, ", ") + "]'"
	}
	return givenText(v)
}

// checkEchoes is check_echoes over the frame written so far (Buffer, then pending), for
// the echoes recorded from index `from`.
func (ctx *EncodeContext) checkEchoes(from int) error {
	frame := append(append([]byte(nil), ctx.Buffer...), ctx.pending...)
	for _, e := range ctx.echoes[from:] {
		if e.field.Source != "" {
			// PS-637 (CR-2026-196): a `$remaining` field's value is the bytes after it.
			n := len(frame) - e.pos
			if n < 0 {
				n = 0
			}
			found, ok := frameSourceValue(e.field, n, e.endian)
			if !ok || !frameSourceAgrees(e.given, found) {
				return frameSourceMismatch(e.field, e.given, found, ok)
			}
			continue
		}
		length := e.length
		if length < 0 {
			length = len(frame) - e.pos
			if length < 0 {
				length = 0
			}
		}
		written := "too few bytes"
		if e.pos+length <= len(frame) {
			var found any
			var ok bool
			if isPeekedNumber(e.field) {
				found, ok = peekedNumberValue(e.field, frame[e.pos:e.pos+length], e.endian)
			} else {
				found, ok = echoValue(e.field, frame[e.pos:e.pos+length])
			}
			if ok && reflect.DeepEqual(echoNormal(found), echoNormal(e.given)) {
				continue
			}
			if ok {
				written = echoText(found)
			}
		}
		rule := "PS-589"
		if isPeekedNumber(e.field) {
			rule = "PS-604"
		}
		return fmt.Errorf("Field '%s' is given %s, but the other fields write %s over its span (%s)",
			e.field.Name, echoText(e.given), written, rule)
	}
	return nil
}
