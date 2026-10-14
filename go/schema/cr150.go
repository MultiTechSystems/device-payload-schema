// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

// CR-2026-150 (PS-560, PS-561): an ascii field with `numeral: decimal|hex` reports its
// digit text as an integer, strictly, and writes it back canonically. Mirrors
// decode_numeral, encode_numeral and numeral_errors in tools/schema_interpreter.py.

var numeralPatterns = map[string]*regexp.Regexp{
	"decimal": regexp.MustCompile(`^[+-]?[0-9]+$`),
	"hex":     regexp.MustCompile(`^[0-9A-Fa-f]+$`),
}

// decodeNumeral is the integer the whole text spells, or an error naming the field; never
// a prefix of it, as JavaScript's parseInt would take.
func decodeNumeral(name, text, numeral string) (any, error) {
	if !numeralPatterns[numeral].MatchString(text) {
		return nil, fmt.Errorf("Field '%s': '%s' is not a %s numeral (PS-560)", name, text, numeral)
	}
	base := 10
	if numeral == "hex" {
		base = 16
	}
	n, err := strconv.ParseInt(strings.TrimPrefix(text, "+"), base, 64)
	if err != nil {
		return nil, fmt.Errorf("Field '%s': '%s' is not a %s numeral (PS-560)", name, text, numeral)
	}
	return n, nil
}

// encodeNumeral is the digits of the value in the declared base, uppercase hex, filled with
// 0 to the length after any sign. A negative hex value, or one that does not fit, is a
// PS-168 error naming the field. fixed is -1 where the length is not fixed.
func encodeNumeral(name string, value float64, fixed int, numeral string) (string, error) {
	n := roundToInteger(value) // PS-552
	abs := math.Abs(n)
	var digits string
	if numeral == "hex" {
		digits = strings.ToUpper(strconv.FormatUint(uint64(abs), 16))
	} else {
		digits = strconv.FormatUint(uint64(abs), 10)
	}
	sign := ""
	if n < 0 {
		sign = "-"
	}
	width := fixed
	if width < 0 {
		width = len(sign) + len(digits)
	}
	if fixed < 0 && n < 0 && numeral == "hex" { // PS-168, PS-595
		return "", fmt.Errorf("Field '%s': %s is negative, which a hex numeral cannot write (PS-168)",
			name, givenText(value))
	}
	if (n < 0 && numeral == "hex") || len(sign)+len(digits) > width {
		shown := width
		if fixed < 0 {
			shown = len(digits)
		}
		return "", fmt.Errorf("Field '%s': %s does not fit %d %s digits (PS-168)",
			name, givenText(value), shown, numeral)
	}
	return sign + strings.Repeat("0", width-len(sign)-len(digits)) + digits, nil
}

// checkNumeral is PS-560's load rule over one field mapping: `numeral` is decimal or hex,
// and only on an ascii field, in the spec prototype's text.
func checkNumeral(field map[string]any) error {
	numeral, has := field["numeral"]
	typ, hasType := field["type"]
	if !has || !hasType {
		return nil
	}
	name := "None"
	if s, ok := field["name"].(string); ok {
		name = s
	}
	if strings.ToLower(fmt.Sprint(typ)) != "ascii" {
		return fmt.Errorf("Field '%s': numeral is defined only on an ascii field, not on %v (PS-560)", name, typ)
	}
	if s, ok := numeral.(string); !ok || numeralPatterns[s] == nil {
		shown := fmt.Sprint(numeral)
		if ok {
			shown = "'" + s + "'"
		}
		return fmt.Errorf("Field '%s': numeral must be decimal or hex, not %s (PS-560)", name, shown)
	}
	return nil
}
