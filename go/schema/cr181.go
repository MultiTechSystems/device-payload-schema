package schema

import (
	"encoding/hex"
	"fmt"
	"math"
	"regexp"
	"strings"
)

// CR-2026-181 (PS-081, PS-601, PS-602; PS-527 and PS-551 amended): an encoder writes a
// `bytes`, `hex` or `base64` value exactly as given. A string is read in the field's own
// format - base64, padded or not, for `format: base64` and the base64 type, otherwise hex
// in either case with the declared separator or without - and an array gives integers 0
// to 255. A malformed value, and one whose byte count is not a literal `length`, is
// rejected naming the field, in the spec prototype's words (`byte_string_value`,
// `write_byte_string`). encodeBytes used to cut and pad, cast array elements with byte(),
// turn a non-number element into 0, and write nothing for a value of another kind; the
// hex and base64 types stripped ':' and '-' whatever the field declared.

var (
	hexText    = regexp.MustCompile(`^(?:[0-9A-Fa-f]{2})*$`)
	base64Text = regexp.MustCompile(`^[A-Za-z0-9+/]*={0,2}$`)
)

// isByteStringType reports the types PS-601 and PS-602 govern.
func isByteStringType(t FieldType) bool {
	switch t {
	case TypeBytes, TypeBytesLower, TypeHex, TypeHexLower, TypeHexUpperLower, TypeBase64, TypeBase64Lower:
		return true
	}
	return false
}

func isBase64Field(f Field) bool {
	if f.Type == TypeBase64 || f.Type == TypeBase64Lower {
		return true
	}
	return (f.Type == TypeBytes || f.Type == TypeBytesLower) && f.Format == "base64"
}

// jsonTypeName is the JSON type of an input value, as a PS-081 message names it.
func jsonTypeName(v any) string {
	switch v.(type) {
	case nil:
		return "null"
	case bool:
		return "boolean"
	case map[string]any:
		return "object"
	}
	if _, ok := toFloat64(v); ok {
		return "number"
	}
	return fmt.Sprintf("%T", v)
}

// byteStringInput is the reference's byte_string_input: the bytes a value gives (PS-081),
// or the PS-601 rejection naming the field.
func byteStringInput(f Field, value any) ([]byte, error) {
	switch v := value.(type) {
	case []byte:
		return v, nil
	case []any:
		out := make([]byte, len(v))
		for i, item := range v {
			num, ok := toFloat64(item)
			if !ok || math.IsNaN(num) || num != math.Trunc(num) || num < 0 || num > 255 {
				return nil, fmt.Errorf("Field '%s': an array element is not an integer from 0 to 255 (PS-601)", f.Name)
			}
			out[i] = byte(num)
		}
		return out, nil
	case string:
		if isBase64Field(f) {
			text := strings.TrimSpace(v)
			padded := text + strings.Repeat("=", (4-len(text)%4)%4)
			if !base64Text.MatchString(padded) || len(strings.TrimRight(padded, "="))%4 == 1 {
				return nil, fmt.Errorf("Field '%s': '%s' is not valid base64 (PS-601)", f.Name, v)
			}
			// The alphabet is checked; trailing bits are ignored, as RFC 4648 allows and
			// Python's b64decode does.
			data := lenientBase64(strings.TrimRight(padded, "="))
			return data, nil
		}
		separator := ""
		if f.Type == TypeBytes || f.Type == TypeBytesLower {
			separator = f.Separator
		}
		text := v
		if separator != "" {
			text = strings.ReplaceAll(text, separator, "")
		}
		if !hexText.MatchString(text) {
			with := ""
			if separator != "" {
				with = fmt.Sprintf(" with separator '%s'", separator)
			}
			return nil, fmt.Errorf("Field '%s': '%s' is not well-formed hex%s (PS-601)", f.Name, v, with)
		}
		return hex.DecodeString(text)
	}
	return nil, fmt.Errorf("Field '%s': a %s is not a byte string value (PS-081)", f.Name, jsonTypeName(value))
}

// lenientBase64 decodes base64 text that the alphabet check has already accepted,
// ignoring trailing bits, as Python's b64decode does.
func lenientBase64(text string) []byte {
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	var out []byte
	acc, bits := 0, 0
	for _, c := range text {
		acc = (acc<<6 | strings.IndexRune(alphabet, c)) & 0xFFFF
		bits += 6
		if bits >= 8 {
			bits -= 8
			out = append(out, byte(acc>>bits))
		}
	}
	return out
}

// literalByteLength is a byte string's literal `length`, where it declares one.
func literalByteLength(f Field) (int, bool) {
	if f.LengthRef != "" || !f.LengthDeclared || f.Length < 0 {
		return 0, false
	}
	return f.Length, true
}

// checkByteStringLength is PS-602 for a literal `length`: the value's byte count, never
// cut or padded.
func checkByteStringLength(f Field, data []byte) error {
	if n, ok := literalByteLength(f); ok && len(data) != n {
		return fmt.Errorf("Field '%s' is given %d bytes for a length of %d (PS-602)", f.Name, len(data), n)
	}
	return nil
}

// encodeByteString writes a bytes, hex or base64 value (PS-601, PS-602).
func encodeByteString(f Field, value any, ctx *EncodeContext) error {
	data, err := byteStringInput(f, value)
	if err != nil {
		return err
	}
	if err := checkByteStringLength(f, data); err != nil {
		return err
	}
	ctx.Write(data)
	return nil
}

// deriveByteCount is the reference's _derive_byte_count: a byte string's value fixes the
// field its `length` names (PS-602, PS-551). Derived where the input omits it; a given
// value that differs is rejected.
func deriveByteCount(fields []Field, f Field, data map[string]any, absent func(string) bool,
	set func(string, any), derived map[string]any) error {
	data2, err := byteStringInput(f, data[f.Name])
	if err != nil {
		return err
	}
	count := len(data2)
	name := f.LengthRef
	source, found := sizeSource(fields, f.LengthRef)
	if found {
		name = source.Name
	}
	if absent(name) {
		if found && derivable(source) && !isPeek(fields, source) {
			set(name, float64(count))
		}
		return nil
	}
	given := encodeDiscriminator(f.LengthRef, derived[name], fields)
	if _, isBool := given.(bool); isBool {
		return nil
	}
	if num, ok := toFloat64(given); ok && num != float64(count) {
		return fmt.Errorf("Field '%s' is given %d bytes, but '%s', its length, is %s (PS-602)",
			f.Name, count, name, messageValue(given))
	}
	return nil
}

// byteStringTakes is PS-527 as CR-2026-181 amends it: a byte string field takes a value
// that is well formed and, where its `length` is a literal, of that byte count.
func byteStringTakes(f Field, value any) bool {
	data, err := byteStringInput(f, value)
	return err == nil && checkByteStringLength(f, data) == nil
}

// repeatTakes: a repeat takes a list whose elements its members take, so a list of 3-byte
// ids goes to the case whose element declares `length: 3`, not a 6-byte one declared
// first (the registry's Tektelic orca tracker).
func repeatTakes(f Field, list []any) (bool, bool) {
	exact := true
	for _, item := range list {
		record, ok := item.(map[string]any)
		if f.ElementName != "" {
			record, ok = map[string]any{f.ElementName: item}, true
		}
		if !ok {
			continue
		}
		for _, member := range f.Fields {
			v, present := record[member.Name]
			if member.Name == "" || !present || v == nil {
				continue
			}
			fit, memberExact := caseTakes(member, v)
			if !fit {
				return false, false
			}
			exact = exact && memberExact
		}
	}
	return true, exact
}
