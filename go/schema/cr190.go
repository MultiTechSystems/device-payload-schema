package schema

import (
	"fmt"
	"unicode/utf8"
)

// CR-2026-190 (PS-624, PS-625): an `ascii` field declaring `charset: utf-8` holds UTF-8
// text. The byte rules (length, nul, terminator) apply to the bytes first; what they leave
// is decoded as UTF-8, with no PS-073 warning, and a value is encoded as UTF-8, `length`
// counting bytes. The message texts are the spec prototype's.

// checkCharset is PS-624's load rule over one field mapping: `charset` is `utf-8`, on an
// ascii field.
func checkCharset(field map[string]any) error {
	typ, hasType := field["type"]
	cs, hasCharset := field["charset"]
	if !hasType || !hasCharset {
		return nil
	}
	if s, ok := cs.(string); typ != "ascii" || !ok || s != "utf-8" {
		name := "None"
		if n, ok := field["name"].(string); ok {
			name = n
		}
		return fmt.Errorf("Field '%s': charset is utf-8, on an ascii field (PS-624)", name)
	}
	return nil
}

// decodeUTF8Text applies the field's nul mode to the bytes and decodes what is left as
// UTF-8 (PS-624). A sequence that is not well formed is an error naming the field and the
// position of its first byte within the text.
func decodeUTF8Text(name string, data []byte, nul string) (string, error) {
	runes := make([]rune, len(data))
	for i, b := range data {
		runes[i] = rune(b)
	}
	kept := applyNulMode(string(runes), nul) // PS-534, as for any ascii field
	raw := make([]byte, 0, len(data))
	for _, r := range kept {
		raw = append(raw, byte(r))
	}
	for i := 0; i < len(raw); {
		r, size := utf8.DecodeRune(raw[i:])
		if r == utf8.RuneError && size <= 1 {
			return "", &asciiEncodeError{fmt.Sprintf(
				"Field '%s': the bytes are not well-formed UTF-8 from byte %d of the text (PS-624)", name, i)}
		}
		i += size
	}
	return string(raw), nil
}

// encodeUTF8Text writes a `charset: utf-8` value (PS-625): its UTF-8 bytes, under the same
// terminator, nul: stop and length rules as encodeASCII, with no PS-537 warning.
func encodeUTF8Text(name, text string, fixed int, nul string, terminator int) ([]byte, error) {
	if !utf8.ValidString(text) {
		return nil, &asciiEncodeError{fmt.Sprintf(
			"Field '%s': an ascii value holds a lone surrogate, which has no UTF-8 form (PS-625)", name)}
	}
	out := []byte(text)
	for _, b := range out {
		if terminator >= 0 && int(b) == terminator { // PS-595
			return nil, &asciiEncodeError{fmt.Sprintf(
				"Field '%s': the value holds its terminator byte 0x%02X (PS-595)", name, terminator)}
		}
	}
	if nul == "stop" { // PS-595
		for _, b := range out {
			if b == 0 {
				return nil, &asciiEncodeError{fmt.Sprintf(
					"Field '%s': a nul: stop value holds a 0x00 byte (PS-595)", name)}
			}
		}
	}
	if fixed >= 0 && len(out) > fixed {
		return nil, &asciiEncodeError{fmt.Sprintf(
			"Field '%s': an ascii value of %d bytes is longer than its length of %d (PS-537, PS-625)",
			name, len(out), fixed)}
	}
	if terminator >= 0 {
		return append(out, byte(terminator)), nil // PS-595
	}
	for fixed >= 0 && len(out) < fixed {
		out = append(out, 0)
	}
	return out, nil
}
