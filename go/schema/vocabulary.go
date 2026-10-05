package schema

import (
	"fmt"
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
	"u32le16": true, "s32le16": true,
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
var fieldConstructKeys = []string{"$ref", "flagged", "tlv", "byte_group", "object", "match"}

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
			switch {
			case hasType && (!isString || strings.TrimSpace(spelling) == ""):
				return fmt.Errorf("%s: field declares no type", at)
			case hasType && !isKnownTypeSpelling(spelling):
				return fmt.Errorf("%s: unknown type: %s", at, spelling)
			case !hasType && !hasAnyKey(field, fieldConstructKeys):
				return fmt.Errorf("%s: field declares no type", at)
			}
			if err := check(field["fields"], at+".fields"); err != nil {
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
