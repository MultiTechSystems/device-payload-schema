// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
)

// CR-2026-174 (PS-585 to PS-587): a field of an unsigned integer type may declare
// `checksum: {algo, seed?, from?, on_mismatch?}`. A decoder computes the check value over
// the frame's bytes from `from` to the byte before the field and compares it with the value
// read; an encoder computes it over the finished frame and writes it. Mirrors
// compute_checksum, checksum_field_errors, checksum_mismatch, fill_checksums and
// _checksum_placeholder in tools/schema_interpreter.py.

// checksumAlgos is each algorithm and the bits its result needs.
var checksumAlgos = map[string]int{"sum8": 8, "xor8": 8, "crc8/smbus": 8, "crc16/kermit": 16}

// checksumKeys are the keys a checksum mapping may carry, beside `description` and `x-*`.
var checksumKeys = map[string]bool{"algo": true, "seed": true, "from": true, "on_mismatch": true}

// checksumWidths are the types a checksum may be declared on, by width in bits.
var checksumWidths = map[string]int{"u8": 8, "u16": 16, "u24": 24, "u32": 32, "u64": 64,
	"uint8": 8, "uint16": 16, "uint24": 24, "uint32": 32, "uint64": 64}

// checksumSpec is a field's checksum, as checked at load.
type checksumSpec struct {
	algo  string
	seed  int
	from  int
	warn  bool
	width int // the field's width in bits
}

// computeChecksum is the four algorithms. Over "123456789", crc8/smbus is 0xF4 and
// crc16/kermit 0x2189, the catalogue check values.
func computeChecksum(algo string, data []byte, seed int) uint64 {
	switch algo {
	case "sum8":
		sum := seed
		for _, b := range data {
			sum += int(b)
		}
		return uint64(sum & 0xFF)
	case "xor8":
		value := seed
		for _, b := range data {
			value ^= int(b)
		}
		return uint64(value)
	case "crc8/smbus": // poly 0x07, init 0, not reflected, no final XOR
		var crc byte
		for _, b := range data {
			crc ^= b
			for i := 0; i < 8; i++ {
				if crc&0x80 != 0 {
					crc = crc<<1 ^ 0x07
				} else {
					crc <<= 1
				}
			}
		}
		return uint64(crc)
	case "crc16/kermit": // poly 0x1021 reflected (0x8408), init 0, no final XOR
		var crc uint16
		for _, b := range data {
			crc ^= uint16(b)
			for i := 0; i < 8; i++ {
				if crc&1 != 0 {
					crc = crc>>1 ^ 0x8408
				} else {
					crc >>= 1
				}
			}
		}
		return uint64(crc)
	}
	return 0
}

// checksumInt reads a seed or `from` as an integer: an int, or a whole float64 as a
// JSON-authored schema gives it. A boolean is not one.
func checksumInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case uint64:
		if n > math.MaxInt32 {
			return 0, false
		}
		return int(n), true
	case float64:
		if n == math.Trunc(n) && math.Abs(n) < 1<<53 {
			return int(n), true
		}
	}
	return 0, false
}

// checkChecksum is PS-585's load rule for one field mapping, the first failure only, in
// the spec prototype's order and text.
func checkChecksum(field map[string]any) error {
	raw, has := field["checksum"]
	if !has {
		return nil
	}
	name := "<unnamed>"
	if n, ok := field["name"]; ok {
		name = fmt.Sprint(n)
	}
	spec := asStringMap(raw)
	if spec == nil {
		return fmt.Errorf("The checksum of '%s' is a mapping, such as {algo: xor8} (PS-585)", name)
	}
	keys := make([]string, 0, len(spec))
	for key := range spec {
		keys = append(keys, key)
	}
	sort.Strings(keys) // a Go map has no source order; the first unknown key sorted
	for _, key := range keys {
		if !checksumKeys[key] && key != "description" && !strings.HasPrefix(key, "x-") {
			return fmt.Errorf("`%s` is not a key of the checksum of '%s' (PS-448)", key, name)
		}
	}
	typ, _ := field["type"].(string)
	width, ok := checksumWidths[typ]
	if !ok {
		return fmt.Errorf("Field '%s': a checksum is declared on an unsigned integer type (u8 to u64), not %s (PS-585)",
			name, pyRepr(field["type"]))
	}
	algo, _ := spec["algo"].(string)
	need, known := checksumAlgos[algo]
	if !known {
		return fmt.Errorf("The checksum of '%s': `algo` is one of crc16/kermit, crc8/smbus, sum8, xor8, not %s (PS-585)",
			name, pyRepr(spec["algo"]))
	}
	if width < need {
		return fmt.Errorf("Field '%s': %s cannot hold a %s result (PS-585)", name, typ, algo)
	}
	if _, hasSeed := spec["seed"]; hasSeed && algo != "sum8" && algo != "xor8" {
		return fmt.Errorf("The checksum of '%s': `seed` applies to sum8 and xor8, not %s (PS-585)", name, algo)
	}
	if v, hasSeed := spec["seed"]; hasSeed {
		if seed, ok := checksumInt(v); !ok || seed < 0 || seed > 255 {
			return fmt.Errorf("The checksum of '%s': `seed` is a byte, 0 to 255 (PS-585)", name)
		}
	}
	if v, hasFrom := spec["from"]; hasFrom {
		if from, ok := checksumInt(v); !ok || from < 0 {
			return fmt.Errorf("The checksum of '%s': `from` is a byte offset, 0 or more (PS-585)", name)
		}
	}
	if v, hasMode := spec["on_mismatch"]; hasMode && v != "error" && v != "warn" {
		return fmt.Errorf("The checksum of '%s': `on_mismatch` is `error` or `warn` (PS-586)", name)
	}
	return nil
}

// checksumOf is a field's checksum, nil where it declares none. The load check has
// already held it to PS-585.
func checksumOf(field Field) *checksumSpec {
	spec := asStringMap(field.decl["checksum"])
	if spec == nil {
		return nil
	}
	typ, _ := field.decl["type"].(string)
	out := &checksumSpec{width: checksumWidths[typ]}
	out.algo, _ = spec["algo"].(string)
	out.seed, _ = checksumInt(spec["seed"])
	out.from, _ = checksumInt(spec["from"])
	out.warn = spec["on_mismatch"] == "warn"
	return out
}

// verifyChecksum is PS-586 on decode: the checksum over the frame's bytes from `from` to
// the byte before the field, which starts at pos, against the value read. A mismatch is
// an error, or a warning with `on_mismatch: warn`; a range starting at or after the field
// covers nothing and is an error (PS-608, CR-2026-185).
func (ctx *DecodeContext) verifyChecksum(field Field, spec *checksumSpec, pos int, carried uint64) error {
	if spec.from >= pos {
		return fmt.Errorf("Field '%s': the checksum range starts at byte %d, at or after the field at byte %d, so it covers nothing (PS-608)",
			field.Name, spec.from, pos)
	}
	computed := computeChecksum(spec.algo, ctx.Data[spec.from:pos], spec.seed)
	if computed == carried {
		return nil
	}
	msg := fmt.Sprintf("Field '%s': %s over bytes %d to %d is 0x%X, but the frame carries 0x%X (PS-586)",
		field.Name, spec.algo, spec.from, pos-1, computed, carried)
	if spec.warn {
		ctx.Warnings = append(ctx.Warnings, msg)
		return nil
	}
	return errors.New(msg)
}

// checksumPlace is a checksum written as a placeholder, at an absolute position of the
// frame, filled in when the frame is finished (PS-587).
type checksumPlace struct {
	pos    int
	field  Field
	spec   *checksumSpec
	endian string
}

// checksumUnwritten is PS-587's rejection: the bytes the checksum covers are not all
// written when it is reached.
func checksumUnwritten(name string) error {
	return fmt.Errorf("Field '%s': the bytes its checksum covers are not all written when it is reached (PS-587)", name)
}

// placeChecksum writes a checksum field's zero placeholder and records it. The input's
// value, internal or not, is never read (PS-587). Rejected where bits a peek left pending
// would be ORed into it, where the field sits in a tlv entry or a repeat element (encoded
// in a branch, apart from the frame; PS-608 rejects both at load), or where its range
// starts past it. A range starting where the field is covers nothing (PS-608). A repeat's
// trailer is written at its frame offset, so a checksum there is placed (CR-2026-185).
func (ctx *EncodeContext) placeChecksum(field Field, spec *checksumSpec) error {
	if ctx.checksums == nil || len(ctx.pending) > 0 {
		return checksumUnwritten(field.Name)
	}
	pos := len(ctx.Buffer)
	if spec.from > pos {
		return checksumUnwritten(field.Name)
	}
	if spec.from == pos {
		return fmt.Errorf("Field '%s': the checksum range starts at byte %d, where the field is, so it covers nothing (PS-608)",
			field.Name, spec.from)
	}
	endian := field.Endian
	if endian == "" {
		endian = ctx.Endian
	}
	ctx.Write(make([]byte, spec.width/8))
	*ctx.checksums = append(*ctx.checksums, checksumPlace{pos, field, spec, endian})
	return nil
}

// fillChecksums computes each placed checksum over the finished frame, earliest first, so
// an outer checksum covers an inner one's value.
func (ctx *EncodeContext) fillChecksums() error {
	if ctx.checksums == nil {
		return nil
	}
	placed := append([]checksumPlace{}, *ctx.checksums...)
	sort.SliceStable(placed, func(i, j int) bool { return placed[i].pos < placed[j].pos })
	for _, c := range placed {
		if c.spec.from > c.pos {
			return checksumUnwritten(c.field.Name)
		}
		value := computeChecksum(c.spec.algo, ctx.Buffer[c.spec.from:c.pos], c.spec.seed)
		size := c.spec.width / 8
		copy(ctx.Buffer[c.pos:c.pos+size], encodeUint(value, size, c.endian))
	}
	return nil
}
