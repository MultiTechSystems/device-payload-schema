package schema

// 0.5.2 wave 5: a lookup default that names the value it could not map (CR-2026-060).
// Each function mirrors the one of the same purpose in tools/schema_interpreter.py.

import (
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

// lookupValueToken is what a mapping's `default` substitutes with the value (PS-406).
const lookupValueToken = "${value}"

// lookupTemplate is the field's mapping default where it carries ${value}, else "".
// Only a mapping's default is a template (PS-408).
func lookupTemplate(field Field) string {
	if field.LookupDefault != nil && strings.Contains(*field.LookupDefault, lookupValueToken) {
		return *field.LookupDefault
	}
	return ""
}

// formatLookupValue writes a value as JavaScript's String(number) does, which is what
// the reference interpreter and the generated codec write (PS-406, PS-280): the
// shortest round-tripping digits, fixed for 1e-6 <= |v| < 1e21, and 1e-7 / 1.5e+21
// outside that range.
func formatLookupValue(v float64) string {
	if v == 0 {
		return "0"
	}
	abs := math.Abs(v)
	if abs >= 1e-6 && abs < 1e21 {
		return strconv.FormatFloat(v, 'f', -1, 64)
	}
	text := strconv.FormatFloat(v, 'e', -1, 64) // e.g. 1.5e-07
	mantissa, exponent, _ := strings.Cut(text, "e")
	sign := exponent[:1]
	digits := strings.TrimLeft(exponent[1:], "0")
	if digits == "" {
		digits = "0"
	}
	return mantissa + "e" + sign + digits
}

// lookupTemplateNumber is a decimal as formatLookupValue writes one.
const lookupTemplateNumber = `(-?\d+(?:\.\d+)?(?:e[-+]?\d+)?)`

// matchLookupTemplate recovers the number a ${value} default wrote into text (PS-409).
// Every ${value} in the template must hold the same number.
func matchLookupTemplate(template, text string) (float64, bool) {
	parts := strings.Split(template, lookupValueToken)
	pattern := "^" + regexp.QuoteMeta(parts[0]) + lookupTemplateNumber
	for _, part := range parts[1 : len(parts)-1] {
		pattern += regexp.QuoteMeta(part) + lookupTemplateNumber
	}
	pattern += regexp.QuoteMeta(parts[len(parts)-1]) + "$"
	match := regexp.MustCompile(pattern).FindStringSubmatch(text)
	if match == nil {
		return 0, false
	}
	// Go's regexp has no back-references, so the repeats are compared here.
	for _, repeat := range match[2:] {
		if repeat != match[1] {
			return 0, false
		}
	}
	value, err := strconv.ParseFloat(match[1], 64)
	return value, err == nil
}

// checkLookupTemplate is PS-407: a ${value} default reports a string, so every label
// must be one. Checked on the raw field, because Field.Lookup keeps string labels only.
func checkLookupTemplate(field map[string]any, at string) error {
	var entries map[string]any
	switch lookup := field["lookup"].(type) {
	case map[string]any:
		entries = lookup
	case map[any]any:
		entries = make(map[string]any, len(lookup))
		for k, v := range lookup {
			entries[fmt.Sprint(k)] = v
		}
	default:
		return nil
	}
	template, ok := entries["default"].(string)
	if !ok || !strings.Contains(template, lookupValueToken) {
		return nil
	}
	numeric := 0
	for k, v := range entries {
		if _, isString := v.(string); k != "default" && !isString {
			numeric++
		}
	}
	if numeric > 0 {
		return fmt.Errorf("%s: a lookup whose default carries ${value} reports a string, so its labels must be strings; %d are not (PS-407)", at, numeric)
	}
	return nil
}
