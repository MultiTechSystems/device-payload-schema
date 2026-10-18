package schema

import (
	"fmt"
	"strings"
	"unicode/utf8"
)

// CR-2026-199 (PS-641, PS-642): an `ascii` field declaring `trim: true` loses leading and
// trailing U+0020, U+0009, U+000A and U+000D, after its nul mode, terminator and charset; an
// encoder pads it with spaces in place of 0x00 where the field declares no `nul`. The
// message text is the spec prototype's.

// trimCharacters is the whitespace `trim: true` removes from both ends of a text.
const trimCharacters = " \t\n\r"

// checkTrim is PS-641's load rule over one field mapping: `trim` is true or false, on an
// ascii field.
func checkTrim(field map[string]any) error {
	typ, hasType := field["type"]
	t, hasTrim := field["trim"]
	if !hasType || !hasTrim {
		return nil
	}
	if _, ok := t.(bool); typ != "ascii" || !ok {
		name := "None"
		if n, ok := field["name"].(string); ok {
			name = n
		}
		return fmt.Errorf("Field '%s': trim is true or false, on an ascii field (PS-641)", name)
	}
	return nil
}

// trimText applies PS-641 to a decoded text where the field declares `trim: true`.
func trimText(field Field, text string) string {
	if !field.Trim {
		return text
	}
	return strings.Trim(text, trimCharacters)
}

// spacePadded turns an encoded value's 0x00 padding into spaces (PS-642) where the field is
// trimmed, declares no `nul` and has a fixed length; `text` is the value written.
func spacePadded(field Field, data []byte, text string, fixed, terminator int) []byte {
	if !field.Trim || field.Nul != "" || fixed < 0 || terminator >= 0 {
		return data
	}
	n := utf8.RuneCountInString(text) // ISO 8859-1: one byte per character
	if field.Charset == "utf-8" {
		n = len(text)
	}
	for i := n; i < len(data); i++ {
		data[i] = ' '
	}
	return data
}
