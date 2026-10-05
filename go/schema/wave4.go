// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"math"
	"math/big"
)

// 0.5.2 wave 4: the word-ordered float and the fourth ordering (CR-2026-046, -047), the
// MCCI minifloats (CR-2026-063) and the named encodings (CR-2026-064). Each mirrors the
// function of the same purpose in tools/schema_interpreter.py.

const (
	TypeF32LE16   FieldType = "f32le16"
	TypeU32BE16LE FieldType = "u32be16le"
	TypeS32BE16LE FieldType = "s32be16le"
	TypeF32BE16LE FieldType = "f32be16le"
	TypeUFlt16    FieldType = "uflt16"
	TypeSFlt16    FieldType = "sflt16"
	TypeSFlt24    FieldType = "sflt24"
)

// wordOrdered gives each word-ordered type's unit order and kind (PS-271, PS-362,
// PS-363): "le16" is the low 16-bit unit first, each unit big-endian; "be16le" the high
// unit first, each unit little-endian. The kind is u, s or f (an IEEE 754 binary32).
var wordOrdered = map[FieldType][2]string{
	TypeU32LE16: {"le16", "u"}, TypeS32LE16: {"le16", "s"}, TypeF32LE16: {"le16", "f"},
	TypeU32BE16LE: {"be16le", "u"}, TypeS32BE16LE: {"be16le", "s"}, TypeF32BE16LE: {"be16le", "f"},
}

func readWordOrdered(t FieldType, data []byte) any {
	layout := wordOrdered[t]
	var word uint32
	if layout[0] == "le16" {
		word = uint32(data[0])<<8 | uint32(data[1]) | (uint32(data[2])<<8|uint32(data[3]))<<16
	} else {
		word = (uint32(data[1])<<8|uint32(data[0]))<<16 | uint32(data[3])<<8 | uint32(data[2])
	}
	switch layout[1] {
	case "f":
		return float64(math.Float32frombits(word))
	case "s":
		return int64(int32(word))
	}
	return uint64(word)
}

func writeWordOrdered(t FieldType, value float64) []byte {
	layout := wordOrdered[t]
	var word uint32
	if layout[1] == "f" {
		word = math.Float32bits(float32(value))
	} else {
		word = uint32(int64(math.RoundToEven(value)))
	}
	high, low := word>>16, word&0xFFFF
	if layout[0] == "le16" {
		return []byte{byte(low >> 8), byte(low), byte(high >> 8), byte(high)}
	}
	return []byte{byte(high), byte(high >> 8), byte(low), byte(low >> 8)}
}

// minifloatSizes are the widths of the MCCI minifloats in bytes.
var minifloatSizes = map[FieldType]int{TypeUFlt16: 2, TypeSFlt16: 2, TypeSFlt24: 3}

// decodeMinifloat is the value of a minifloat word (PS-417 to PS-419), and false where it
// has none (an sflt24 exponent of 127). Never an IEEE half-precision decode (PS-421).
func decodeMinifloat(t FieldType, word uint64) (float64, bool) {
	var v float64
	switch t {
	case TypeUFlt16:
		e, f := word>>12, word&0x0FFF
		v = float64(f) / 4096 * math.Pow(2, float64(e)-15)
	case TypeSFlt16:
		e, f := (word>>11)&0x0F, word&0x07FF
		v = float64(f) / 2048 * math.Pow(2, float64(e)-15)
		if word&0x8000 != 0 {
			v = -v
		}
	default:
		e, f := (word>>16)&0x7F, word&0xFFFF
		if e == 127 {
			return 0, false
		}
		if e == 0 {
			v = float64(f) / 65536 * math.Pow(2, -62)
		} else {
			v = (1 + float64(f)/65536) * math.Pow(2, float64(e)-63)
		}
		if word&0x800000 != 0 {
			v = -v
		}
	}
	if v == 0 {
		return 0, true // -0 is reported as 0 (PS-418)
	}
	return v, true
}

// roundHalfEvenRat rounds an exact rational to the nearest integer, ties to even.
func roundHalfEvenRat(r *big.Rat) int64 {
	floor := new(big.Int).Quo(r.Num(), r.Denom())
	if r.Sign() < 0 && new(big.Int).Mul(floor, r.Denom()).Cmp(r.Num()) != 0 {
		floor.Sub(floor, big.NewInt(1))
	}
	rest := new(big.Rat).Sub(r, new(big.Rat).SetInt(floor))
	half := big.NewRat(1, 2)
	if c := rest.Cmp(half); c > 0 || (c == 0 && floor.Bit(0) == 1) {
		floor.Add(floor, big.NewInt(1))
	}
	return floor.Int64()
}

// encodeMinifloat is the word for a value: the smallest exponent whose fraction fits,
// ties to even, and an error outside the type's range (PS-420).
func encodeMinifloat(t FieldType, value float64) (uint64, error) {
	exact := new(big.Rat)
	exact.SetFloat64(value)
	negative := exact.Sign() < 0
	magnitude := new(big.Rat).Abs(exact)
	pow2 := func(n int) *big.Rat {
		if n >= 0 {
			return new(big.Rat).SetInt(new(big.Int).Lsh(big.NewInt(1), uint(n)))
		}
		return new(big.Rat).SetFrac(big.NewInt(1), new(big.Int).Lsh(big.NewInt(1), uint(-n)))
	}
	if t == TypeUFlt16 || t == TypeSFlt16 {
		if negative && t == TypeUFlt16 {
			return 0, fmt.Errorf("%v is negative; uflt16 holds [0, 1) (PS-420)", value)
		}
		bits := 12
		if t == TypeSFlt16 {
			bits = 11
		}
		for e := 0; e < 16; e++ {
			f := roundHalfEvenRat(new(big.Rat).Mul(magnitude, pow2(bits+15-e)))
			if f < 1<<uint(bits) {
				word := uint64(e)<<uint(bits) | uint64(f)
				if negative {
					word |= 0x8000
				}
				return word, nil
			}
		}
		return 0, fmt.Errorf("%v is outside the range of %s (PS-420)", value, t)
	}
	var sign uint64
	if negative {
		sign = 0x800000
	}
	if magnitude.Sign() == 0 {
		return sign, nil
	}
	for e := 1; e < 127; e++ {
		if magnitude.Cmp(pow2(e-62)) < 0 {
			if magnitude.Cmp(pow2(e-63)) < 0 {
				break
			}
			fr := new(big.Rat).Sub(new(big.Rat).Quo(magnitude, pow2(e-63)), big.NewRat(1, 1))
			f := roundHalfEvenRat(new(big.Rat).Mul(fr, big.NewRat(65536, 1)))
			if f == 65536 {
				continue
			}
			return sign | uint64(e)<<16 | uint64(f), nil
		}
	}
	if magnitude.Cmp(pow2(-62)) >= 0 {
		return 0, fmt.Errorf("%v is outside the range of sflt24 (PS-420)", value)
	}
	f := roundHalfEvenRat(new(big.Rat).Mul(magnitude, new(big.Rat).Mul(big.NewRat(65536, 1), pow2(62))))
	if f >= 65536 {
		return sign | 1<<16, nil
	}
	return sign | uint64(f), nil
}

// decodeEncoding applies a named encoding to the unsigned integer read (PS-422 to PS-425).
func decodeEncoding(raw uint64, encoding string, size int) (int64, error) {
	bits := uint(size * 8)
	switch encoding {
	case "sign_magnitude":
		sign := uint64(1) << (bits - 1)
		if raw&sign != 0 {
			return -int64(raw &^ sign), nil
		}
		return int64(raw), nil
	case "bcd":
		var out int64
		for i := int(size*2) - 1; i >= 0; i-- {
			digit := (raw >> uint(4*i)) & 0x0F
			if digit > 9 {
				return 0, fmt.Errorf("invalid BCD digit %d (PS-424)", digit)
			}
			out = out*10 + int64(digit)
		}
		return out, nil
	case "gray":
		out := raw
		for shift := uint(1); shift < bits; shift <<= 1 {
			out ^= out >> shift
		}
		return int64(out), nil
	}
	return int64(raw), nil
}

// encodeEncoding is the inverse, rejecting a value with no representation (PS-463).
func encodeEncoding(value int64, encoding string, size int) (uint64, error) {
	bits := uint(size * 8)
	switch encoding {
	case "sign_magnitude":
		sign := uint64(1) << (bits - 1)
		magnitude := value
		if magnitude < 0 {
			magnitude = -magnitude
		}
		if uint64(magnitude) > sign-1 {
			return 0, fmt.Errorf("%d has no %d-bit sign-magnitude form (PS-463)", value, bits)
		}
		if value < 0 {
			return sign | uint64(magnitude), nil
		}
		return uint64(value), nil
	case "bcd":
		if value < 0 || float64(value) > math.Pow(10, float64(size*2))-1 {
			return 0, fmt.Errorf("%d has no %d-bit BCD form (PS-463)", value, bits)
		}
		var out uint64
		for shift := uint(0); value > 0; shift += 4 {
			out |= uint64(value%10) << shift
			value /= 10
		}
		return out, nil
	case "gray":
		v := uint64(value)
		return v ^ (v >> 1), nil
	}
	return uint64(value), nil
}
