package schema

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// A range case key, "2..5" or "12.1..35.4" (PS-398): two numbers, each a decimal integer,
// a hexadecimal integer or a decimal with a fraction or exponent, either of them negative.
// A bound is compared as the number the YAML spells, an integer staying an integer, so a
// key such as "12.1..35.4" holds a computed 12.1 and a reading of 35.4 exactly.
const rangeNumber = `[+-]?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)`

var rangeKeyPattern = regexp.MustCompile(`^\s*(` + rangeNumber + `)\s*\.\.\s*(` + rangeNumber + `)\s*$`)

// rangeBound is one bound of a range key: an exact integer where the text is one.
type rangeBound struct {
	i     int64
	f     float64
	isInt bool
}

func parseRangeBound(text string) rangeBound {
	text = strings.TrimSpace(text)
	sign, digits := int64(1), strings.TrimPrefix(text, "+")
	if strings.HasPrefix(digits, "-") {
		sign, digits = -1, digits[1:]
	}
	base := 10
	if strings.HasPrefix(strings.ToLower(digits), "0x") {
		digits, base = digits[2:], 16
	}
	if base == 16 || !strings.ContainsAny(digits, ".eE") {
		if n, err := strconv.ParseInt(digits, base, 64); err == nil {
			return rangeBound{i: sign * n, f: float64(sign * n), isInt: true}
		}
	}
	f, _ := strconv.ParseFloat(text, 64)
	return rangeBound{f: f}
}

// parseRangeKey reads a range case key, or reports that the text is not one.
func parseRangeKey(text string) (lo, hi rangeBound, ok bool) {
	m := rangeKeyPattern.FindStringSubmatch(text)
	if m == nil {
		return rangeBound{}, rangeBound{}, false
	}
	return parseRangeBound(m[1]), parseRangeBound(m[2]), true
}

// containsInt reports whether an integer discriminator lies in lo..hi inclusive.
func rangeContainsInt(value int64, lo, hi rangeBound) bool {
	if lo.isInt && hi.isInt {
		return value >= lo.i && value <= hi.i
	}
	return float64(value) >= lo.f && float64(value) <= hi.f
}

// rangeContainsFloat reports whether any discriminator lies in lo..hi inclusive.
func rangeContainsFloat(value float64, lo, hi rangeBound) bool {
	return value >= lo.f && value <= hi.f
}

// rangeKeyError says why a case key spelled like a range is not one (PS-398, PS-151), or
// "" where it is one or is no range at all. A malformed key matched nothing and raised no
// error, so every value fell through to the default.
func rangeKeyError(key string) string {
	if !strings.Contains(key, "..") || strings.HasPrefix(strings.TrimSpace(key), "[") {
		return ""
	}
	lo, hi, ok := parseRangeKey(key)
	if !ok {
		return "a range key is `start..end` with two numbers, each an integer or a decimal, such as 2..5 or 12.1..35.4 (PS-398)"
	}
	if lo.f > hi.f {
		return "a range key has start <= end (PS-151, PS-398)"
	}
	return ""
}

func rangeKeyLoadError(path, key string) error {
	if problem := rangeKeyError(key); problem != "" {
		return fmt.Errorf("%s.cases[%s]: %s", path, key, problem)
	}
	return nil
}
