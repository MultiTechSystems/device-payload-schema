// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"math/big"
	"regexp"
	"strconv"
	"strings"
)

// CR-2026-170 (PS-430, PS-582, PS-096, PS-583): a bitfield_string part format is decimal,
// sdecimal, hex or hex:upper, optionally followed by `:N`, a minimum width from 1 to 32
// that never cuts. `sdecimal` reads its bits as two's complement. An encoder rejects a
// segment the part's width cannot hold. Mirrors parse_part_format, format_part_number,
// format_bitfield_part and parse_bitfield_segment in tools/schema_interpreter.py; the
// formatting is formatPartNumber alone, so CR-2026-176's templates can reuse it.

var partFormatPattern = regexp.MustCompile(`^(decimal|sdecimal|hex:upper|hex)(?::([1-9][0-9]?))?$`)

var segmentPatterns = map[string]*regexp.Regexp{
	"decimal":   regexp.MustCompile(`^[0-9]+$`),
	"sdecimal":  regexp.MustCompile(`^-?[0-9]+$`),
	"hex":       regexp.MustCompile(`^[0-9A-Fa-f]+$`),
	"hex:upper": regexp.MustCompile(`^[0-9A-Fa-f]+$`),
}

// parsePartFormat is a part format's base and minimum width (0 where it declares none),
// or PS-430's rejection in the spec prototype's text.
func parsePartFormat(format any) (string, int, error) {
	if text, ok := format.(string); ok {
		if m := partFormatPattern.FindStringSubmatch(text); m != nil {
			if m[2] == "" {
				return m[1], 0, nil
			}
			if width, _ := strconv.Atoi(m[2]); width <= 32 {
				return m[1], width, nil
			}
		}
	}
	return "", 0, fmt.Errorf("bitfield_string part format must be decimal, sdecimal, hex or "+
		"hex:upper, with an optional :N width from 1 to 32, not %s (PS-430)", pyRepr(format))
}

// formatPartNumber writes a magnitude in a part format's base - hex lower or upper case,
// else decimal - with a leading `-` where neg, zero-filled on the left to width digits and
// never cut (PS-430, PS-582).
func formatPartNumber(neg bool, magnitude uint64, base string, width int) string {
	var digits string
	switch base {
	case "hex":
		digits = strconv.FormatUint(magnitude, 16)
	case "hex:upper":
		digits = strings.ToUpper(strconv.FormatUint(magnitude, 16))
	default:
		digits = strconv.FormatUint(magnitude, 10)
	}
	if len(digits) < width {
		digits = strings.Repeat("0", width-len(digits)) + digits
	}
	if neg {
		return "-" + digits
	}
	return digits
}

// formatBitfieldPart writes one part from its unsigned bits; sdecimal reads them as two's
// complement of the part's width (PS-582). The format was checked at load.
func formatBitfieldPart(raw uint64, bits int, format any) string {
	base, width, err := parsePartFormat(format)
	if err != nil {
		base = "decimal"
	}
	if base == "sdecimal" && bits > 0 && bits <= 64 && raw>>(bits-1)&1 == 1 {
		// The magnitude of a negative two's-complement value of `bits` bits.
		mask := ^uint64(0)
		if bits < 64 {
			mask = (uint64(1) << bits) - 1
		}
		return formatPartNumber(true, (^raw+1)&mask, base, width)
	}
	return formatPartNumber(false, raw, base, width)
}

// parseBitfieldSegment is one segment's unsigned bits (PS-096, PS-431, PS-583): leading
// zeros are accepted, hex without regard to case, a `-` only on sdecimal; a value the
// part's width cannot hold is rejected naming the field and the segment, never masked.
func parseBitfieldSegment(segment string, bits int, format any, name string) (uint64, error) {
	base, _, err := parsePartFormat(format)
	if err != nil {
		return 0, err
	}
	text := strings.TrimSpace(segment)
	if !segmentPatterns[base].MatchString(text) {
		return 0, fmt.Errorf("Field '%s': segment %s is not a %s part (PS-096)", name, pyRepr(segment), base)
	}
	radix := 10
	if strings.HasPrefix(base, "hex") {
		radix = 16
	}
	number, _ := new(big.Int).SetString(text, radix)
	one := big.NewInt(1)
	low, high := new(big.Int), new(big.Int).Sub(new(big.Int).Lsh(one, uint(bits)), one)
	if base == "sdecimal" {
		half := new(big.Int).Lsh(one, uint(bits-1))
		low.Neg(half)
		high.Sub(half, one)
	}
	if number.Cmp(low) < 0 || number.Cmp(high) > 0 {
		return 0, fmt.Errorf("Field '%s': segment %s does not fit a %d-bit %s part, %s to %s (PS-583)",
			name, pyRepr(segment), bits, base, low.String(), high.String())
	}
	if number.Sign() < 0 {
		number.Add(number, new(big.Int).Lsh(one, uint(bits)))
	}
	return number.Uint64(), nil
}
