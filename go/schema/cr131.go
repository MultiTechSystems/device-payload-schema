package schema

import (
	"fmt"
	"strconv"
)

// wholeIntegerBytes is the width of each whole-integer type a byte_group member may name
// (PS-070): `u16` is `u16[0:15]`. The word-ordered types are not among them; their bytes
// are not one value. Aliases are canonicalised through typeAliases first.
var wholeIntegerBytes = map[string]int{
	"u8": 1, "u16": 2, "u24": 3, "u32": 4, "u64": 8,
	"s8": 1, "s16": 2, "s24": 3, "s32": 4, "s64": 8,
}

// memberRange is the part of a byte_group's assembled value one member covers.
type memberRange struct {
	baseBytes int
	low, high int
	signed    bool
	isBool    bool
}

// byteGroupMemberRange is a member's base width and bits (PS-070, CR-2026-131). A member
// is a bit range; a whole integer, which is its full range; or a bool with `bit`, one bit
// of a u8. Its bits are numbered in the group's assembled value (PS-364), and it reads
// from the group's start: no member moves the position. Anything else is an error.
func byteGroupMemberRange(typ string, bit any, hasBit bool, name string) (memberRange, error) {
	if alias, ok := typeAliases[typ]; ok {
		typ = string(alias)
	}
	if m := bitRangePattern.FindStringSubmatch(typ); m != nil {
		bits, _ := strconv.Atoi(m[1][1:])
		low, _ := strconv.Atoi(m[2])
		high, _ := strconv.Atoi(m[3])
		if high < low {
			low, high = high, low
		}
		base := bits / 8
		if base < 1 {
			base = 1
		}
		return memberRange{base, low, high, m[1][0] == 's', false}, nil
	}
	if size, ok := wholeIntegerBytes[typ]; ok {
		return memberRange{size, 0, size*8 - 1, typ[0] == 's', false}, nil
	}
	if typ == "bool" && hasBit {
		if b, ok := toInt(bit); ok {
			if b < 0 || b > 7 {
				return memberRange{}, fmt.Errorf("byte_group member '%s' is a bool at bit %d; a bool member is one bit of a u8, 0 to 7 (PS-070)", name, b)
			}
			return memberRange{1, b, b, false, true}, nil
		}
	}
	return memberRange{}, fmt.Errorf("byte_group member '%s' is not a bit range, a whole integer or a bool with `bit` (PS-364)", name)
}

// checkByteGroup holds a group's load-time rules: every member a kind PS-070 admits, no
// two members on the same bits of the assembled value (PS-397), and a `size` no
// narrower than the widest member's base (PS-070, CR-2026-131).
func checkByteGroup(field map[string]any, at string) error {
	group := field["byte_group"]
	members, ok := group.([]any)
	size, hasSize := field["size"]
	if !ok {
		gm := asStringMap(group)
		members, _ = gm["fields"].([]any)
		size, hasSize = gm["size"]
	}
	type seenMember struct {
		name string
		low  int
		high int
	}
	var seen []seenMember
	widest := 1
	for _, raw := range members {
		member := asStringMap(raw)
		if member == nil {
			continue
		}
		name, _ := member["name"].(string)
		typ, _ := member["type"].(string)
		bit, hasBit := member["bit"]
		r, err := byteGroupMemberRange(typ, bit, hasBit, name)
		if err != nil {
			return fmt.Errorf("%s.byte_group: %v", at, err)
		}
		for _, other := range seen {
			if r.low <= other.high && other.low <= r.high {
				return fmt.Errorf("%s.byte_group: members '%s' and '%s' overlap (PS-397)", at, other.name, name)
			}
		}
		seen = append(seen, seenMember{name, r.low, r.high})
		if r.baseBytes > widest {
			widest = r.baseBytes
		}
	}
	if hasSize {
		if declared, ok := toInt(size); ok && declared < widest {
			return fmt.Errorf("byte_group declares size %d, narrower than its widest member's %d bytes (PS-070)", declared, widest)
		}
	}
	return nil
}

// fieldMemberRange is byteGroupMemberRange for a parsed member.
func fieldMemberRange(member Field) memberRange {
	if member.Type == TypeBoolLower || member.Type == TypeBool {
		return memberRange{1, member.Bit, member.Bit, false, true}
	}
	r, err := byteGroupMemberRange(string(member.Type), nil, false, member.Name)
	if err != nil {
		// Rejected at load (checkByteGroup); a Field built in code reads one byte.
		return memberRange{1, 0, 7, false, false}
	}
	return r
}

// byteGroupWidth is the bytes a byte_group covers (PS-070, CR-2026-131): its widest
// member's base type, whichever bytes that member's bits fall in, widened by `size`. A
// size narrower than a member is rejected at load. Decoding read a group with no size as
// one byte, so a u16[8:15] member decoded from the wrong byte, while encoding already
// widened to the base - the two directions disagreed.
func byteGroupWidth(field Field) int {
	width := field.Size
	if width < 1 {
		width = 1
	}
	for _, member := range field.ByteGroup {
		if w := fieldMemberRange(member).baseBytes; w > width {
			width = w
		}
	}
	return width
}
