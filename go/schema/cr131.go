package schema

import "strconv"

// groupIntegerTypes are the integer types whose width a full-width byte_group member
// contributes to its group. Aliases are canonicalised before a Field is built.
var groupIntegerTypes = map[string]bool{
	"u8": true, "u16": true, "u24": true, "u32": true, "u64": true,
	"s8": true, "s16": true, "s24": true, "s32": true, "s64": true,
	"u32le16": true, "s32le16": true, "u32be16le": true, "s32be16le": true,
}

// byteGroupMemberWidth is the bytes a byte_group member's base type spans: u16[0:3] and
// u16 are 2. A bool member is one byte, and so is anything unrecognised.
func byteGroupMemberWidth(member Field) int {
	if m := bitRangePattern.FindStringSubmatch(string(member.Type)); m != nil {
		bits, _ := strconv.Atoi(m[1][1:])
		if bits/8 > 1 {
			return bits / 8
		}
		return 1
	}
	if groupIntegerTypes[string(member.Type)] {
		return inferLengthFromType(member.Type)
	}
	return 1
}

// byteGroupWidth is the bytes a byte_group covers (PS-070, CR-2026-131): with no `size`,
// its widest member's base type, whichever bytes that member's bits fall in; `size` may
// widen it, never narrow it. Decoding read a group with no size as one byte, so a
// u16[8:15] member decoded from the wrong byte, while encoding already widened to the
// base - the two directions disagreed.
func byteGroupWidth(field Field) int {
	width := field.Size
	if width < 1 {
		width = 1
	}
	for _, member := range field.ByteGroup {
		if w := byteGroupMemberWidth(member); w > width {
			width = w
		}
	}
	return width
}
