package schema

// CR-2026-195 (PS-525 amended, PS-632 to PS-634): flat lists. `on_repeat: concat` on a tlv
// reports a name as one array however many entries report it, splicing a list value
// (PS-632); `flatten` on a repeat with `fields` reports its element's inner repeat's
// arrays as one list (PS-633). Both decode; a concat whose case reports a list, and any
// flatten, are refused for encoding (PS-634). The messages are the spec prototype's.

import (
	"fmt"
	"strings"
)

// concatValue is one more report of a concat name: appended to the array, its elements
// spliced where it is itself a list (PS-632).
func concatValue(existing any, ok bool, v any) []any {
	var out []any
	if ok {
		out, _ = existing.([]any)
	}
	if list, isList := v.([]any); isList {
		return append(out, list...)
	}
	return append(out, v)
}

// concatListError is PS-634 for a concat name whose case reports a list.
func concatListError(name string) error {
	return fmt.Errorf("tlv: '%s' is concatenated from lists, which cannot be split back into entries; the schema decodes but is not encoded (PS-634)", name)
}

// flattenEncodeError is PS-634 for a repeat declaring flatten.
func flattenEncodeError(name string) error {
	return fmt.Errorf("Repeat '%s' flattens its groups into one list, which cannot be split back into groups; the schema decodes but is not encoded (PS-634)", name)
}

// flattenError is check_flatten for one field mapping (PS-633): `flatten` names a repeat
// among the element's fields of a repeat with `fields`, whose other element fields are
// internal.
func flattenError(field map[string]any) error {
	target, has := field["flatten"]
	if !has {
		return nil
	}
	if _, typed := field["type"]; !typed {
		return nil
	}
	where := fmt.Sprintf("Repeat '%s'", pyNameOrNone(field["name"]))
	var fields []any
	if field["type"] == "repeat" {
		fields, _ = field["fields"].([]any)
	}
	var inner map[string]any
	for _, item := range fields {
		if f := asStringMap(item); f != nil && f["name"] == target {
			inner = f
			break
		}
	}
	if fields == nil || inner == nil || inner["type"] != "repeat" {
		return fmt.Errorf("%s: flatten names a repeat among its element's fields (PS-633)", where)
	}
	var others []string
	for _, item := range fields {
		f := asStringMap(item)
		if f == nil {
			continue
		}
		if _, typed := f["type"]; !typed || f["name"] == target {
			continue
		}
		name, isText := f["name"].(string)
		if !isText {
			if f["name"] == nil {
				continue // the prototype reads a missing name as internal ('_')
			}
			name = fmt.Sprint(f["name"])
		}
		if strings.HasPrefix(name, "_") {
			continue
		}
		others = append(others, "'"+name+"'")
	}
	if len(others) > 0 {
		return fmt.Errorf("%s: with flatten, its element's other fields are internal; %s would be reported (PS-633)", where, strings.Join(others, ", "))
	}
	return nil
}

// pyNameOrNone writes a field's name as the prototype's f-string does: None where absent.
func pyNameOrNone(v any) string {
	if v == nil {
		return "None"
	}
	return fmt.Sprint(v)
}
