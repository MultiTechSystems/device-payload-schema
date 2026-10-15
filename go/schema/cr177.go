// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"strings"
)

// CR-2026-177 (PS-534, PS-072, PS-593 to PS-595): an ascii field's `nul` mode (trailing,
// stop, drop) and a `terminator` byte in place of `length`. Mirrors apply_nul_mode,
// terminated_span and ascii_text_errors in tools/schema_interpreter.py.

// applyNulMode is PS-534: trailing removes trailing 0x00 bytes and keeps the rest, stop ends
// the text at the first 0x00, drop removes every 0x00. Whitespace is never trimmed.
func applyNulMode(text, mode string) string {
	switch mode {
	case "stop":
		if i := strings.IndexByte(text, 0); i >= 0 {
			return text[:i]
		}
		return text
	case "drop":
		return strings.ReplaceAll(text, "\x00", "")
	}
	return strings.TrimRight(text, "\x00")
}

// terminatedSpan is PS-593: the number of text bytes before the field's terminator, and
// whether one was found. A payload that ends before one is an underflow, which the field
// names (PS-535), unless the field declares `end_terminates: true` (PS-619, CR-2026-188):
// then the end of the data (ctx.Data stops at a reserve or a tlv entry's end) ends it.
func terminatedSpan(field *Field, ctx *DecodeContext) (int, bool, error) {
	for i := ctx.Offset; i < len(ctx.Data); i++ {
		if int(ctx.Data[i]) == field.Terminator {
			return i - ctx.Offset, true, nil
		}
	}
	if field.EndTerminates {
		if n := len(ctx.Data) - ctx.Offset; n > 0 {
			return n, false, nil
		}
		return 0, false, nil
	}
	return 0, false, &BufferUnderflowError{Detail: fmt.Sprintf(
		"no terminator byte 0x%02X before the end of the payload (PS-593)", field.Terminator)}
}

// terminatorValue reads a declared terminator that is a whole number 0 to 255.
func terminatorValue(v any) (int, bool) {
	switch t := v.(type) {
	case int:
		return t, true
	case int64:
		return int(t), true
	case float64:
		if t == float64(int(t)) {
			return int(t), true
		}
	}
	return 0, false
}

// checkAsciiText is the load rule over one field mapping, in the spec prototype's text:
// `nul` and `terminator` are defined on an ascii field only, `nul` is trailing, stop or
// drop, a terminator is a byte 0 to 255 declared in place of `length`, and an ascii field
// declares one of the two.
func checkAsciiText(field map[string]any) error {
	typ, hasType := field["type"]
	if !hasType {
		return nil
	}
	nul, hasNul := field["nul"]
	term, hasTerm := field["terminator"]
	name := "None"
	if s, ok := field["name"].(string); ok {
		name = s
	}
	isAscii := strings.ToLower(fmt.Sprint(typ)) == "ascii"
	if hasNul || hasTerm {
		if !isAscii {
			return fmt.Errorf("Field '%s': nul and terminator are defined only on an ascii field, not on %v (PS-534, PS-593)", name, typ)
		}
		if hasNul {
			if s, ok := nul.(string); !ok || (s != "trailing" && s != "stop" && s != "drop") {
				return fmt.Errorf("Field '%s': nul must be trailing, stop or drop, not %s (PS-534)", name, sampledRepr(nul))
			}
		}
		if hasTerm {
			if n, ok := terminatorValue(term); !ok || n < 0 || n > 255 {
				return fmt.Errorf("Field '%s': terminator must be a byte value 0 to 255, not %s (PS-593)", name, sampledRepr(term))
			}
			if _, both := field["length"]; both {
				return fmt.Errorf("Field '%s': an ascii field declares length or terminator, not both (PS-593)", name)
			}
		}
	}
	// PS-619 (CR-2026-188): consume_terminator and end_terminates need a terminator and
	// are true or false.
	consumeT, hasConsume := field["consume_terminator"]
	endT, hasEnd := field["end_terminates"]
	if hasConsume || hasEnd {
		if !hasTerm {
			return fmt.Errorf("Field '%s': consume_terminator and end_terminates belong to a field with a terminator (PS-619)", name)
		}
		if _, ok := consumeT.(bool); hasConsume && !ok {
			return fmt.Errorf("Field '%s': consume_terminator is true or false (PS-619)", name)
		}
		if _, ok := endT.(bool); hasEnd && !ok {
			return fmt.Errorf("Field '%s': end_terminates is true or false (PS-619)", name)
		}
	}
	if isAscii {
		if _, hasLen := field["length"]; !hasLen && !hasTerm {
			return fmt.Errorf("Field '%s': ascii type requires 'length' field, or 'terminator' (PS-072)", name)
		}
	}
	return nil
}
