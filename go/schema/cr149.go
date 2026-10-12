// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"math"
)

// CR-2026-149 (PS-558, PS-559): bf16, the upper half of an IEEE 754 binary32. Mirrors
// decode_bf16 and encode_bf16 in tools/schema_interpreter.py.

const TypeBF16 FieldType = "bf16"

// bf16Max is the largest finite bf16, 0x7F7F: (2 - 2^-7) * 2^127.
const bf16Max = (2 - 1.0/128) * (1 << 127) * 1.0

// decodeBF16 is the binary32 whose upper half is the word and whose lower half is zero.
func decodeBF16(word uint64) float64 {
	return float64(math.Float32frombits(uint32(word) << 16))
}

// encodeBF16 is the nearest bf16 to the binary64 value, a tie to the even fraction,
// rounded once and never through a binary32. A finite value that rounds past the largest
// bf16 is a PS-168 error naming the field; NaN and the infinities are their words.
func encodeBF16(value float64, name string) (uint64, error) {
	value = positiveZero(value) // PS-566
	if math.IsNaN(value) {
		return 0x7FC0, nil
	}
	var sign uint64
	if math.Signbit(value) {
		sign = 0x8000
	}
	magnitude := math.Abs(value)
	if math.IsInf(magnitude, 0) {
		return sign | 0x7F80, nil
	}
	mantissa, exponent := math.Frexp(magnitude) // magnitude = mantissa * 2^exponent
	var word uint64
	if magnitude == 0 || exponent-1 < -126 {
		word = uint64(math.RoundToEven(math.Ldexp(magnitude, 133))) // fraction * 2^-133
	} else {
		fraction := int(math.RoundToEven(mantissa * 256)) // 128..256, the leading bit included
		if fraction == 256 {
			fraction, exponent = 128, exponent+1
		}
		word = uint64(exponent+126)<<7 | uint64(fraction-128)
	}
	if word >= 0x7F80 {
		return 0, fmt.Errorf("Field '%s': %s is outside the range of bf16, %s to %s (PS-168)",
			name, givenText(value), givenText(-bf16Max), givenText(bf16Max))
	}
	if word == 0 {
		return 0, nil // CR-2026-157: a negative value that rounds to zero is the zero word
	}
	return sign | word, nil
}

// positiveZero turns a negative zero into the positive one and changes nothing else
// (PS-566): a decoder reports -0 as 0 and an encoder writes the positive-zero word for -0.0,
// for a decoded -0 and for one its own arithmetic produced. Go does not fold `v + 0`, but a
// comparison says what is meant.
func positiveZero(v float64) float64 {
	if v == 0 {
		return 0
	}
	return v
}
