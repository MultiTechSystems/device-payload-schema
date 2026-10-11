// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"errors"
	"fmt"
	"strings"
)

// CR-2026-114 and CR-2026-115 (la-payload-schema main c932961): a read past the end
// names its field (PS-535), and `ascii` text is ISO 8859-1 both ways (PS-534, PS-537,
// PS-538). The message texts are the spec prototype's and the reference's, so a
// cross-language diff compares equal strings.

// BufferUnderflowError is a read or skip past the end of the payload, less any
// `reserve` in force (PS-535). The read raises it unnamed; the innermost field that made
// the read restates it with nameUnderflow, and every enclosing construct passes it
// through unchanged.
type BufferUnderflowError struct {
	// Detail is the read's own text, without the field.
	Detail string
	// Named is set once the error identifies its field.
	Named bool
	msg   string
}

func (e *BufferUnderflowError) Error() string {
	if e.Named {
		return e.msg
	}
	return e.Detail
}

// underflowNeed is a short read of n bytes at pos, with end the end of the readable
// region.
func underflowNeed(n, pos, end int) *BufferUnderflowError {
	remaining := end - pos
	if remaining < 0 {
		remaining = 0
	}
	return &BufferUnderflowError{Detail: fmt.Sprintf(
		"Buffer underflow: need %d bytes at offset %d, but only %d remaining", n, pos, remaining)}
}

// underflowPeek is a short read by a bit range or a bool, which read their byte(s)
// without consuming them.
func underflowPeek(pos int) *BufferUnderflowError {
	return &BufferUnderflowError{Detail: fmt.Sprintf("Buffer underflow at peek offset %d", pos)}
}

// underflowLabel is how a PS-535 error identifies a field: its name, else what it is.
func underflowLabel(name, kind string) string {
	if name != "" {
		return "'" + name + "'"
	}
	if kind == "" {
		kind = "field"
	}
	return "an unnamed " + kind
}

// nameUnderflow restates an unnamed short read as the error of the field that made it
// (PS-535). Any other error, or one already named, is returned unchanged.
func nameUnderflow(err error, name, kind string) error {
	var under *BufferUnderflowError
	if !errors.As(err, &under) || under.Named {
		return err
	}
	return &BufferUnderflowError{
		Detail: under.Detail,
		Named:  true,
		msg:    fmt.Sprintf("Field %s: %s (PS-535)", underflowLabel(name, kind), under.Detail),
	}
}

// isBufferUnderflow reports whether err is a short read, named or not.
func isBufferUnderflow(err error) bool {
	var under *BufferUnderflowError
	return errors.As(err, &under)
}

// memberBaseBytes is the bytes a byte_group member's own base reads: `u16[0:3]` two.
func memberBaseBytes(member Field) int {
	if m := bitRangePattern.FindStringSubmatch(string(member.Type)); m != nil {
		var bits int
		fmt.Sscanf(m[1][1:], "%d", &bits)
		if bits/8 > 0 {
			return bits / 8
		}
	}
	return 1
}

// byteGroupUnderflow names a byte_group's short read as the spec prototype does: each
// member reads the group's bytes from its start, so the first member whose base does not
// fit made the read. Where every base fits and the group's size does not, the group did.
func byteGroupUnderflow(field Field, size int, ctx *DecodeContext) error {
	remaining := ctx.Remaining()
	for _, member := range field.ByteGroup {
		if memberBaseBytes(member) > remaining {
			return nameUnderflow(underflowPeek(ctx.Offset), member.Name, string(member.Type))
		}
	}
	return nameUnderflow(underflowNeed(size, ctx.Offset, len(ctx.Data)), "", "byte_group")
}

// asciiHighByteWarning is PS-073's warning for a byte above 0x7F (PS-538).
func asciiHighByteWarning(name string) string {
	return fmt.Sprintf("Field '%s': a byte above 0x7F in an ascii value (PS-073, PS-538)", name)
}

// decodeASCII reports each byte as the ISO 8859-1 character of the same number, so the
// value is valid text and re-encodes to the same bytes, and drops trailing 0x00 padding
// only (PS-534, PS-538). A byte above 0x7F adds PS-073's warning. Go returned the bytes
// themselves, which are not a valid UTF-8 string and so not a valid JSON one.
func decodeASCII(name string, data []byte, ctx *DecodeContext) string {
	runes := make([]rune, len(data))
	high := false
	for i, b := range data {
		runes[i] = rune(b)
		if b > 0x7F {
			high = true
		}
	}
	if high {
		ctx.Warnings = append(ctx.Warnings, asciiHighByteWarning(name))
	}
	return strings.TrimRight(string(runes), "\x00")
}

// encodeASCII writes `ascii` text (PS-537): a character above U+00FF, or a value longer
// than a fixed length, is rejected naming the field; U+0080..U+00FF is written as the
// byte of the same number with a warning; a short value is padded with 0x00. fixed < 0
// means the length is not fixed (`remaining`, a reference), and the value supplies it.
// Every encoder truncated silently, and wrote UTF-8, which could split a character.
func encodeASCII(name, text string, fixed int, ctx *EncodeContext) ([]byte, error) {
	out := make([]byte, 0, len(text))
	high := false
	for _, r := range text {
		if r > 0xFF {
			return nil, &asciiEncodeError{fmt.Sprintf(
				"Field '%s': an ascii value holds a character above U+00FF, which has no byte (PS-537)",
				name)}
		}
		if r > 0x7F {
			high = true
		}
		out = append(out, byte(r))
	}
	if fixed >= 0 && len(out) > fixed {
		return nil, &asciiEncodeError{fmt.Sprintf(
			"Field '%s': an ascii value of %d bytes is longer than its length of %d (PS-537)",
			name, len(out), fixed)}
	}
	if high && ctx != nil {
		ctx.Warnings = append(ctx.Warnings, fmt.Sprintf(
			"Field '%s': a character above U+007F in an ascii value (PS-537)", name))
	}
	for fixed >= 0 && len(out) < fixed {
		out = append(out, 0)
	}
	return out, nil
}

// asciiEncodeError is a PS-537 rejection. Its text names the field already, so an
// enclosing encoder reports it verbatim rather than prefixing it.
type asciiEncodeError struct{ msg string }

func (e *asciiEncodeError) Error() string { return e.msg }

// isNamedFieldError reports whether err's text already names its field in full and must
// be reported verbatim.
func isNamedFieldError(err error) bool {
	var asciiErr *asciiEncodeError
	if errors.As(err, &asciiErr) {
		return true
	}
	var under *BufferUnderflowError
	return errors.As(err, &under) && under.Named
}
