package schema

import (
	"errors"
	"fmt"
	"sort"
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
	"f16": true, "bf16": true, "f32": true, "f64": true,
	"udec": true, "sdec": true,
	"bool": true, "boolean": true, "bytes": true, "string": true, "ascii": true,
	"hex": true, "base64": true,
	"skip": true, "enum": true, "bitfield_string": true,
	"number": true, "integer": true,
	"object": true, "repeat": true,
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
var fieldConstructKeys = []string{"$ref", "flagged", "tlv", "byte_group", "match", "records"}

// checkTypeVocabulary walks every field list of a raw schema and reports the first
// field whose type is unknown (PS-327) or absent with no construct to stand in for
// it (PS-334).
func checkTypeVocabulary(raw map[string]any) error {
	var check func(fields any, path string) error
	// The cases of a `match` or `tlv` construct, whose bodies are field lists
	// (PS-347, PS-441). A bare string such as `5: skip` was parsed as an empty case, so
	// a match decoded nothing for it and a tlv dropped the case and skipped the tag.
	checkCases := func(cases any, path string) error {
		caseMap := asStringMap(cases)
		for _, key := range sortedKeys(caseMap) {
			body := caseMap[key]
			if _, ok := body.([]any); !ok {
				return fmt.Errorf("%s.cases[%s]: a case body is a field list; write [] for a case that reads nothing (PS-441)", path, key)
			}
			if err := rangeKeyLoadError(path, key); err != nil { // PS-398, PS-151
				return err
			}
			if err := check(body, fmt.Sprintf("%s[%s]", path, key)); err != nil {
				return err
			}
		}
		return nil
	}
	check = func(fields any, path string) error {
		list, ok := fields.([]any)
		if !ok {
			return nil
		}
		// Names (and variables) this list has declared an object or repeat under: a key
		// template cannot write one (PS-532).
		aggregates := map[string]bool{}
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
			// The legacy `type: match` is withdrawn, not unknown: say what replaces it.
			if isString && spelling == "match" {
				return errors.New(withdrawnTypeMatch)
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
			if template, ok := field["name_from"].(string); ok {
				for _, m := range nameFromPattern.FindAllStringSubmatch(template, -1) {
					if aggregates[m[1]] {
						return fmt.Errorf("%s: template %q references %q, which is not a number, string or boolean and cannot be written into a key (PS-532)", at, template, m[1])
					}
				}
			}
			// PS-532, PS-590 (CR-2026-176): a template string writes no object or repeat.
			if err := checkTemplateAggregates(field, aggregates); err != nil {
				return err
			}
			if spelling == "object" || spelling == "repeat" {
				if name != "" {
					aggregates[name] = true
				}
				if v, ok := field["var"].(string); ok && v != "" {
					aggregates[v] = true
				}
			}
			if err := check(field["fields"], at+".fields"); err != nil {
				return err
			}
			if err := check(field["trailer"], at+".trailer"); err != nil {
				return err
			}
			// PS-541: each of a `records` entry's field lists, held to the same rules.
			if bodies, ok := field["records"].([]any); ok {
				for r, body := range bodies {
					if err := check(body, fmt.Sprintf("%s.records[%d]", at, r)); err != nil {
						return err
					}
				}
			}
			// PS-528: a repeat's `element` is one field definition, held to the same rules.
			if element := asStringMap(field["element"]); element != nil {
				// PS-545 first: a construct or typeless element is named as such, not as
				// a field that declares no type.
				if field["type"] == "repeat" {
					if err := elementRepeatError(field); err != nil {
						return err
					}
				}
				if err := check([]any{element}, at+".element"); err != nil {
					return err
				}
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
	if err := checkWithdrawnPorts(raw); err != nil { // CR-2026-159
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
	"floor", "ceiling", "op"}

// checkFieldRules holds the per-field rules a schema is rejected for at load, as the
// specification requires of each.
// byteGroupMemberBits and checkByteGroupOverlap live in cr131.go (PS-070, PS-397).

// literalForbidden are the keys a literal must not carry (PS-358).
var literalForbidden = []string{"ref", "polynomial", "compute", "lookup", "transform", "mult", "div", "add"}

// unsignedTypes are the types an `encoding` may sit on (PS-422).
var unsignedTypes = map[string]bool{"u8": true, "u16": true, "u24": true, "u32": true, "u64": true,
	"uint8": true, "uint16": true, "uint24": true, "uint32": true, "uint64": true}

func checkFieldRules(field map[string]any, at string) error {
	// CR-2026-159: formula, encode_formula, and min/max on a field that is no repeat.
	if err := checkWithdrawnField(field); err != nil {
		return err
	}
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
	// PS-361, PS-590, PS-430 (CR-2026-176): `template` on a string field without `value`,
	// a string field declaring one of the two, and each ${name:format} a part format.
	if err := checkStringTemplate(field); err != nil {
		return err
	}
	// PS-610 to PS-614 (CR-2026-186): a numeric field's `time` mapping.
	if err := checkTimeField(field); err != nil {
		return err
	}
	// PS-560 (CR-2026-150): numeral is decimal or hex, on an ascii field only.
	if err := checkNumeral(field); err != nil {
		return err
	}
	// PS-616, PS-617 (CR-2026-187): bit_labels on an unsigned integer or bit range.
	if err := checkBitLabels(field); err != nil {
		return err
	}
	// PS-621 (CR-2026-189): packed_bits and bit_offset on a repeat of integers only.
	if err := packedRepeatError(field); err != nil {
		return err
	}
	// PS-633 (CR-2026-195): flatten names an inner repeat; the element's others internal.
	if err := flattenError(field); err != nil {
		return err
	}
	// PS-534, PS-593 (CR-2026-177): nul and terminator on an ascii field.
	if err := checkAsciiText(field); err != nil {
		return err
	}
	// PS-624 (CR-2026-190): charset is utf-8, on an ascii field.
	if err := checkCharset(field); err != nil {
		return err
	}
	// PS-635 (CR-2026-196): source names a value of the frame, on a type: number field.
	if err := checkFrameSource(field); err != nil {
		return err
	}
	// PS-585 (CR-2026-174): a checksum on an unsigned integer wide enough for its algo.
	if err := checkChecksum(field); err != nil {
		return err
	}
	// PS-510 (CR-2026-102): ipso and senml are mappings.
	if err := checkAnnotations(field); err != nil {
		return err
	}
	// PS-407: a ${value} lookup default needs string labels.
	if err := checkLookupTemplate(field, at); err != nil {
		return err
	}
	// PS-430 (CR-2026-170): a bitfield_string part is decimal, sdecimal, hex or hex:upper,
	// with an optional :N width; the spec prototype's text, unprefixed.
	if parts, ok := field["parts"].([]any); ok && field["type"] == "bitfield_string" {
		for _, raw := range parts {
			if part, ok := raw.([]any); ok && len(part) > 2 {
				if _, _, err := parsePartFormat(part[2]); err != nil {
					return err
				}
			}
		}
	}
	// PS-519: `boolean` is a literal type only, so it declares `value`. PS-520: the
	// value is a boolean; "true", 1 and null are refused.
	if field["type"] == "boolean" {
		value, ok := field["value"]
		if !ok {
			return fmt.Errorf("%s: type boolean is a literal and declares no value (PS-519)", at)
		}
		if _, isBool := value.(bool); !isBool {
			return fmt.Errorf("%s: a boolean literal's value must be true or false, not %v (PS-520)", at, value)
		}
	}
	// PS-358: a literal's value matches its type, and it carries no arithmetic.
	if value, ok := field["value"]; ok && (field["type"] == "string" || field["type"] == "number" || field["type"] == "boolean") {
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
	// PS-399, PS-416: a match declares exactly one discriminator source - `field`,
	// `length` or `remaining`. With both of the first two, `field` won and the `length`
	// byte was left unread, misaligning every later field. The same check holds the
	// sentinel and out_of_range forms (PS-427, PS-428); see wave6b.go.
	if err := checkWave6bFieldRules(field, at); err != nil {
		return err
	}
	if group, ok := field["byte_group"]; ok {
		if err := checkByteGroup(field, at); err != nil { // PS-070, PS-397
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
	// PS-445 (CR-2026-071): a guard belongs to a computed field. On a field read from the
	// payload, or a literal, it was ignored with success - a schema meaning "no reading
	// below this" decoded every value. `sentinel` and `out_of_range: omit` say that for
	// a read value.
	if _, ok := field["guard"]; ok {
		typ, _ := field["type"].(string)
		if (typ != "number" && typ != "integer") || !hasAnyKey(field, []string{"ref", "compute"}) {
			return fmt.Errorf("%s: a guard is declared only on a computed field (ref or "+
				"compute); for a value read from the payload use sentinel or "+
				"out_of_range: omit (PS-445)", at)
		}
	}
	if transform, ok := field["transform"]; ok {
		if err := checkTransformStages(transform, at); err != nil {
			return err
		}
	}
	return nil
}

// transformOpParameters are the keys of an `op:` stage that are parameters of its one
// operation rather than operations of their own (PS-452).
var transformOpParameters = []string{"decimals", "ties"}

// checkTransformStages holds PS-390 and PS-452 (CR-2026-073): each stage is a mapping
// holding exactly one operation the language defines, checked on the raw map.
//
// It has to be the raw map. The parser builds a Transform from a stage by hand, one key
// at a time, so `{add: 1, mult: 2}` became a struct with both set and
// applyTransformStages ran mult then add - 1 decoded as 3, where Python gave 7 by its own
// order and the generated codec applied only one. And `{add: 1, sub: 2}` passed the old
// "names some operation" check on its `add` while `sub`, which no implementation is to
// read, was applied too.
func checkTransformStages(transform any, at string) error {
	stages, ok := transform.([]any)
	if !ok {
		return fmt.Errorf("%s: `transform` must be a list of stages (PS-102)", at)
	}
	for i, raw := range stages {
		stage := asStringMap(raw)
		where := fmt.Sprintf("%s.transform[%d]", at, i)
		if stage == nil {
			return fmt.Errorf("%s: a transform stage must be a mapping holding one operation (PS-452)", where)
		}
		// PS-390: `{round: n}` is not a stage. Go accepted it and did nothing,
		// reporting the unrounded value with success.
		if _, ok := stage["round"]; ok {
			return fmt.Errorf("%s: `{round: n}` is not a transform stage; write "+
				"{op: round, decimals: n} (PS-390)", where)
		}
		// CR-2026-159: `clamp` is withdrawn, with what replaces it.
		if _, ok := stage["clamp"]; ok {
			return errors.New(withdrawnClamp)
		}
		var unknown, operations []string
		for _, key := range sortedKeys(stage) {
			switch {
			case containsString(transformOperations, key):
				operations = append(operations, key)
			case !containsString(transformOpParameters, key):
				unknown = append(unknown, key)
			}
		}
		if len(unknown) > 0 {
			return fmt.Errorf("%s: transform stage names no operation of the PS-115 table: %s (PS-390)",
				where, strings.Join(unknown, ", "))
		}
		if len(operations) != 1 {
			held := "none"
			if len(operations) > 0 {
				held = strings.Join(operations, ", ")
			}
			return fmt.Errorf("%s: a transform stage must hold exactly one operation, and holds %s (PS-452)",
				where, held)
		}
		op, isOp := stage["op"]
		if !isOp {
			if hasAnyKey(stage, transformOpParameters) {
				return fmt.Errorf("%s: `decimals` and `ties` belong to an `op:` stage (PS-452)", where)
			}
			continue
		}
		if op != "round" {
			return fmt.Errorf("%s: transform stage names an unknown operation %v (PS-390)", where, op)
		}
		if ties, ok := stage["ties"]; ok && ties != "even" && ties != "away" && ties != "up" {
			return fmt.Errorf("%s: round `ties` must be even, away or up, got %v (PS-390)", where, ties)
		}
	}
	return nil
}

// sortedKeys returns a map's keys in a fixed order, so an error naming several reads
// the same on every run.
func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for key := range m {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
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

// checkAnnotations holds PS-510 (CR-2026-102): a field's `ipso` is a mapping carrying
// `object`, and its `senml` a mapping. `ipso: 3303` loaded and was dropped, so the author
// believed the field annotated and no consumer saw an annotation. The wording is the spec
// prototype's, and Python, Java and C# give it identically. A `semantic` is a string, a
// TTN normalized path (PS-470, CR-2026-179).
func checkAnnotations(field map[string]any) error {
	name, ok := field["name"].(string)
	if !ok {
		name = "?"
	}
	if ipso, has := field["ipso"]; has {
		m := asStringMap(ipso)
		if _, hasObject := m["object"]; m == nil || !hasObject {
			return fmt.Errorf("The ipso of '%s' is a mapping that carries `object`, such as {object: 3303}; a bare object number is not a shorthand (PS-510)", name)
		}
	}
	if senml, has := field["senml"]; has && asStringMap(senml) == nil {
		return fmt.Errorf("The senml of '%s' is a mapping, such as {unit: Cel} (PS-510)", name)
	}
	if semantic, has := field["semantic"]; has {
		// PS-470 (CR-2026-179): a TTN normalized path, so a string.
		if _, ok := semantic.(string); !ok {
			return fmt.Errorf("The semantic of '%s' is a string naming a normalized measurement type, such as air.temperature (PS-470)", name)
		}
	}
	return nil
}
