package schema

import (
	"fmt"
	"strconv"
	"strings"
)

// The type vocabulary of clause 2, closed by CR-2026-037.
//
// A spelling outside it is not a type (PS-326), type names are case-sensitive
// (PS-333), and a field naming one is rejected rather than read as something else
// (PS-327) - with the field and the spelling named (PS-328). This parser accepted a
// private vocabulary of its own: `Byte`, `UInt`, `Float16`, `TLV`, `Switch` and the
// capitalised forms of every type, none of which any other implementation reads.
//
// The capitalised FieldType constants remain, because the compact and binary
// formats build Fields from them internally. Only a schema's own spelling is
// checked here.

// typeAliases maps each alias of the PS-049 table to its canonical spelling, so the
// rest of the package sees one spelling per type. `uint64` and `int64` were missing.
var typeAliases = map[string]FieldType{
	"uint8": TypeU8, "uint16": TypeU16, "uint24": TypeU24, "uint32": TypeU32, "uint64": TypeU64,
	"i8": TypeS8, "int8": TypeS8,
	"i16": TypeS16, "int16": TypeS16,
	"i24": TypeS24, "int24": TypeS24,
	"i32": TypeS32, "int32": TypeS32,
	"i64": TypeS64, "int64": TypeS64,
}

// canonicalTypes is every canonical spelling clause 2 defines, plus the structural
// `type:` values (`object`, `match`, `repeat`, `enum`) and the computed `number` and
// `integer`.
var canonicalTypes = map[string]bool{
	"u8": true, "u16": true, "u24": true, "u32": true, "u64": true,
	"s8": true, "s16": true, "s24": true, "s32": true, "s64": true,
	"u32le16": true, "s32le16": true, "f32le16": true,
	"u32be16le": true, "s32be16le": true, "f32be16le": true,
	"uflt16": true, "sflt16": true, "sflt24": true,
	"f16": true, "f32": true, "f64": true,
	"udec": true, "sdec": true,
	"bool": true, "bytes": true, "string": true, "ascii": true,
	"hex": true, "base64": true,
	"skip": true, "enum": true, "bitfield_string": true,
	"number": true, "integer": true,
	"object": true, "match": true, "repeat": true,
}

// TypeInteger is a computed field whose result is reported as an integer (PS-283).
const TypeInteger FieldType = "integer"

// Nibble-decimal types (PS-329).
const (
	TypeUDec FieldType = "udec"
	TypeSDec FieldType = "sdec"
)

// canonicalFieldType resolves an alias to its canonical spelling and leaves anything
// else alone; whether the spelling exists at all is checkTypeVocabulary's question.
func canonicalFieldType(spelling string) FieldType {
	if canonical, ok := typeAliases[spelling]; ok {
		return canonical
	}
	return FieldType(spelling)
}

func isKnownTypeSpelling(spelling string) bool {
	if canonicalTypes[spelling] {
		return true
	}
	if _, ok := typeAliases[spelling]; ok {
		return true
	}
	return bitRangePattern.MatchString(spelling)
}

// fieldConstructKeys are the keys that make a field a construct rather than a typed
// read, so it carries no `type` of its own.
var fieldConstructKeys = []string{"$ref", "flagged", "tlv", "byte_group", "match"}

// checkTypeVocabulary walks every field list of a raw schema and reports the first
// field whose type is unknown (PS-327) or absent with no construct to stand in for
// it (PS-334).
func checkTypeVocabulary(raw map[string]any) error {
	var check func(fields any, path string) error
	checkCases := func(cases any, path string) error {
		for key, body := range asStringMap(cases) {
			if list, ok := body.([]any); ok {
				if err := check(list, fmt.Sprintf("%s[%s]", path, key)); err != nil {
					return err
				}
			} else if m := asStringMap(body); m != nil {
				if err := check(m["fields"], fmt.Sprintf("%s[%s]", path, key)); err != nil {
					return err
				}
			}
		}
		if list, ok := cases.([]any); ok {
			for i, entry := range list {
				if m := asStringMap(entry); m != nil {
					if err := check(m["fields"], fmt.Sprintf("%s[%d]", path, i)); err != nil {
						return err
					}
				}
			}
		}
		return nil
	}
	check = func(fields any, path string) error {
		list, ok := fields.([]any)
		if !ok {
			return nil
		}
		for i, entry := range list {
			field := asStringMap(entry)
			if field == nil {
				continue
			}
			name, _ := field["name"].(string)
			at := fmt.Sprintf("%s[%d]", path, i)
			if name != "" {
				at = fmt.Sprintf("%s (%s)", at, name)
			}
			typ, hasType := field["type"]
			spelling, isString := typ.(string)
			// PS-466 (CR-2026-074): the `object:` key is withdrawn; a nested group is
			// `type: object`. This parser never read the key, so such a field failed
			// later as a typeless one.
			if objectName, ok := field["object"]; ok && !hasType {
				return fmt.Errorf("%s: the `object:` key is withdrawn; write `type: object` with `name: %v` and `fields` (PS-466)", at, objectName)
			}
			switch {
			case hasType && (!isString || strings.TrimSpace(spelling) == ""):
				return fmt.Errorf("%s: field declares no type", at)
			case hasType && !isKnownTypeSpelling(spelling):
				return fmt.Errorf("%s: unknown type: %s", at, spelling)
			case !hasType && !hasAnyKey(field, fieldConstructKeys):
				return fmt.Errorf("%s: field declares no type", at)
			}
			if err := checkFieldRules(field, at); err != nil {
				return err
			}
			if err := check(field["fields"], at+".fields"); err != nil {
				return err
			}
			if err := check(field["trailer"], at+".trailer"); err != nil {
				return err
			}
			switch group := field["byte_group"].(type) {
			case []any:
				if err := check(group, at+".byte_group"); err != nil {
					return err
				}
			case map[string]any, map[any]any:
				if err := check(asStringMap(group)["fields"], at+".byte_group"); err != nil {
					return err
				}
			}
			for _, key := range []string{"match", "tlv"} {
				if construct := asStringMap(field[key]); construct != nil {
					if err := checkCases(construct["cases"], at+"."+key); err != nil {
						return err
					}
					if err := check(construct["default"], at+"."+key+".default"); err != nil {
						return err
					}
				}
			}
			if err := checkCases(field["cases"], at+".cases"); err != nil {
				return err
			}
			if flagged := asStringMap(field["flagged"]); flagged != nil {
				if groups, ok := flagged["groups"].([]any); ok {
					for g, groupEntry := range groups {
						if err := check(asStringMap(groupEntry)["fields"], fmt.Sprintf("%s.flagged[%d]", at, g)); err != nil {
							return err
						}
					}
				}
			}
		}
		return nil
	}

	if err := checkPortDeclarations(raw); err != nil {
		return err
	}
	if err := check(raw["fields"], "fields"); err != nil {
		return err
	}
	for key, port := range asStringMap(raw["ports"]) {
		if err := check(asStringMap(port)["fields"], "ports."+key); err != nil {
			return err
		}
	}
	for key, def := range asStringMap(raw["definitions"]) {
		if list, ok := def.([]any); ok {
			if err := check(list, "definitions."+key); err != nil {
				return err
			}
		} else if err := check(asStringMap(def)["fields"], "definitions."+key); err != nil {
			return err
		}
	}
	return nil
}

func hasAnyKey(m map[string]any, keys []string) bool {
	for _, key := range keys {
		if _, ok := m[key]; ok {
			return true
		}
	}
	return false
}

// asStringMap normalises the two map shapes a YAML or JSON decode produces.
func asStringMap(v any) map[string]any {
	switch m := v.(type) {
	case map[string]any:
		return m
	case map[any]any:
		out := make(map[string]any, len(m))
		for k, val := range m {
			out[fmt.Sprintf("%v", k)] = val
		}
		return out
	}
	return nil
}

// transformOperations are the stages PS-098 and the PS-115 table define, keyed by name.
// `op` names an operation instead; `round` is the only one (PS-390).
var transformOperations = []string{"add", "mult", "div", "sqrt", "abs", "pow", "log10", "log",
	"floor", "ceiling", "clamp", "op"}

// checkFieldRules holds the per-field rules a schema is rejected for at load, as the
// specification requires of each.
// byteGroupMemberBits is the set of bits a byte_group member covers, counted from the
// group's first bit (most significant bit of its first byte), or nil for a member that
// is not a bit range or a bool. A range's bits are numbered within its own base (PS-058),
// so they are mapped onto the group's bit string before members of different widths
// can be compared.
func byteGroupMemberBits(member map[string]any) map[int]bool {
	typ, _ := member["type"].(string)
	width, start, end := 0, 0, 0
	if m := bitRangePattern.FindStringSubmatch(typ); m != nil {
		width, _ = strconv.Atoi(m[1][1:])
		start, _ = strconv.Atoi(m[2])
		end, _ = strconv.Atoi(m[3])
	} else if typ == "bool" {
		width = 8
		start, _ = toInt(member["bit"])
		end = start
	} else {
		return nil
	}
	bits := map[int]bool{}
	for b := start; b <= end; b++ {
		bits[width-1-b] = true
	}
	return bits
}

// checkByteGroupOverlap holds PS-397: bit ranges within one byte_group must not overlap.
// Overlapping members were accepted, each reported from the same bits.
func checkByteGroupOverlap(group any, at string) error {
	members, ok := group.([]any)
	if !ok {
		members, _ = asStringMap(group)["fields"].([]any)
	}
	type seenMember struct {
		name string
		bits map[int]bool
	}
	var seen []seenMember
	for _, raw := range members {
		member := asStringMap(raw)
		if member == nil {
			continue
		}
		bits := byteGroupMemberBits(member)
		if bits == nil {
			continue
		}
		name, _ := member["name"].(string)
		for _, other := range seen {
			for b := range bits {
				if other.bits[b] {
					return fmt.Errorf("%s.byte_group: members '%s' and '%s' overlap (PS-397)", at, other.name, name)
				}
			}
		}
		seen = append(seen, seenMember{name, bits})
	}
	return nil
}

// literalForbidden are the keys a literal must not carry (PS-358).
var literalForbidden = []string{"ref", "polynomial", "compute", "lookup", "transform", "mult", "div", "add"}

// unsignedTypes are the types an `encoding` may sit on (PS-422).
var unsignedTypes = map[string]bool{"u8": true, "u16": true, "u24": true, "u32": true, "u64": true,
	"uint8": true, "uint16": true, "uint24": true, "uint32": true, "uint64": true}

func checkFieldRules(field map[string]any, at string) error {
	// PS-426: match_value is withdrawn. PS-422: `encoding` is a named code on a uN.
	if _, ok := field["match_value"]; ok {
		return fmt.Errorf("%s: match_value is withdrawn; write a signed type (sN), a signed bit range (sN[start:end]), encoding, match or guard instead (PS-426)", at)
	}
	if encoding, ok := field["encoding"]; ok {
		if encoding != "sign_magnitude" && encoding != "bcd" && encoding != "gray" {
			return fmt.Errorf("%s: encoding %v is not one of sign_magnitude, bcd, gray (PS-422)", at, encoding)
		}
		if typ, _ := field["type"].(string); !unsignedTypes[typ] {
			return fmt.Errorf("%s: encoding applies only to an unsigned integer type uN, not %v (PS-422)", at, field["type"])
		}
	}
	// PS-407: a ${value} lookup default needs string labels.
	if err := checkLookupTemplate(field, at); err != nil {
		return err
	}
	// PS-430: a bitfield_string part is decimal, hex or hex:upper.
	if parts, ok := field["parts"].([]any); ok && field["type"] == "bitfield_string" {
		for _, raw := range parts {
			if part, ok := raw.([]any); ok && len(part) > 2 {
				if format := part[2]; format != "decimal" && format != "hex" && format != "hex:upper" {
					return fmt.Errorf("%s: bitfield_string part format %v is not one of decimal, hex, hex:upper (PS-430)", at, format)
				}
			}
		}
	}
	// PS-358: a literal's value matches its type, and it carries no arithmetic.
	if value, ok := field["value"]; ok && (field["type"] == "string" || field["type"] == "number") {
		_, isString := value.(string)
		_, isBool := value.(bool)
		_, isNumber := toFloat64(value)
		if field["type"] == "string" && !isString {
			return fmt.Errorf("%s: a string literal's value must be a string (PS-358)", at)
		}
		if field["type"] == "number" && (isBool || !isNumber || isString) {
			return fmt.Errorf("%s: a number literal's value must be a number (PS-358)", at)
		}
		for _, key := range literalForbidden {
			if _, has := field[key]; has {
				return fmt.Errorf("%s: a literal must not declare %s (PS-358)", at, key)
			}
		}
	}
	// PS-399: a match declares exactly one discriminator source. With both, `field` won
	// and the `length` byte was left unread, misaligning every later field.
	if match := asStringMap(field["match"]); match != nil {
		_, hasField := match["field"]
		_, hasLength := match["length"]
		if hasField == hasLength {
			return fmt.Errorf("%s.match: a match must declare exactly one of 'field' and 'length' (PS-399)", at)
		}
	}
	if group, ok := field["byte_group"]; ok {
		if err := checkByteGroupOverlap(group, at); err != nil {
			return err
		}
		members, isList := group.([]any)
		if !isList {
			members, _ = asStringMap(group)["fields"].([]any)
		}
		for _, member := range members {
			if _, has := asStringMap(member)["endian"]; has {
				return fmt.Errorf("%s.byte_group: a member declares endian; the group's byte order is declared on the group (PS-364)", at)
			}
		}
	}
	// PS-079, PS-391: a bytes format is one of four, and a separator applies to the two
	// hex ones. `hex:lower` and any other spelling were read here as plain hex.
	if field["type"] == "bytes" {
		format, hasFormat := field["format"]
		if !hasFormat {
			format = "hex"
		}
		switch format {
		case "hex", "hex:upper", "base64", "array":
		default:
			return fmt.Errorf("%s: bytes format %v is not one of hex, hex:upper, base64, array (PS-079)", at, format)
		}
		if _, ok := field["separator"]; ok && format != "hex" && format != "hex:upper" {
			return fmt.Errorf("%s: `separator` applies only to the hex formats, not %v (PS-391)", at, format)
		}
	}
	if stages, ok := field["transform"].([]any); ok {
		for i, raw := range stages {
			stage := asStringMap(raw)
			where := fmt.Sprintf("%s.transform[%d]", at, i)
			if stage == nil {
				return fmt.Errorf("%s: a transform stage must be a mapping", where)
			}
			// PS-390: `{round: n}` is not a stage. Go accepted it and did nothing,
			// reporting the unrounded value with success.
			if _, ok := stage["round"]; ok {
				return fmt.Errorf("%s: `{round: n}` is not a transform stage; write "+
					"{op: round, decimals: n} (PS-390)", where)
			}
			if op, ok := stage["op"]; ok {
				if op != "round" {
					return fmt.Errorf("%s: transform stage names an unknown operation %v (PS-390)", where, op)
				}
				if ties, ok := stage["ties"]; ok && ties != "even" && ties != "away" {
					return fmt.Errorf("%s: round `ties` must be even or away, got %v (PS-390)", where, ties)
				}
			}
			if !hasAnyKey(stage, transformOperations) {
				return fmt.Errorf("%s: transform stage names no operation of the PS-115 table (PS-390)", where)
			}
		}
	}
	return nil
}

// checkPortDeclarations holds PS-018 (a port key is 1 to 255, CR-2026-041) and PS-335 and
// PS-337 (a top-level fPort is such a port, and beside `ports` every key equals it,
// CR-2026-042). The top-level key selects nothing (PS-336) and is never read again.
func checkPortDeclarations(raw map[string]any) error {
	ports := asStringMap(raw["ports"])
	for key := range ports {
		if key == "default" {
			continue
		}
		if n, err := strconv.Atoi(key); err != nil || n < 1 || n > 255 {
			return fmt.Errorf("ports.%s: a port key must be an integer from 1 to 255 or default (PS-018)", key)
		}
	}
	name := "fPort"
	declared, ok := raw["fPort"]
	if !ok {
		name = "fport"
		declared, ok = raw["fport"]
	}
	if !ok {
		return nil
	}
	port, isInt := declared.(int)
	if !isInt || port < 1 || port > 255 {
		return fmt.Errorf("top-level %s must be an integer from 1 to 255, got %v (PS-335)", name, declared)
	}
	for key := range ports {
		if key != strconv.Itoa(port) {
			return fmt.Errorf("top-level %s is %d but ports also declares %s; every ports key must equal it (PS-337)", name, port, key)
		}
	}
	return nil
}
