package schema

// CR-2026-161 (PS-570, PS-571): `_meta` carries each entry's reported type as `reports`,
// and a lookup's labels are of one type. reportsOf, lookupLabels, jsonKind and
// checkLookupTypes mirror the functions of the same purpose in tools/schema_interpreter.py,
// whose wording and rules are the spec prototype's (reports_of, check_lookup_types).

import (
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
)

var integerTypeNames = func() map[string]bool {
	out := map[string]bool{
		"u32le16": true, "s32le16": true, "u32be16le": true, "s32be16le": true,
	}
	for _, n := range []string{"8", "16", "24", "32", "64"} {
		for _, p := range []string{"u", "s", "i", "uint", "int"} {
			out[p+n] = true
		}
	}
	return out
}()

var reportsFloatTypes = map[string]bool{
	"number": true, "f16": true, "bf16": true, "f32": true, "f64": true, "f32le16": true,
	"f32be16le": true, "uflt16": true, "sflt16": true, "sflt24": true, "udec": true, "sdec": true,
}

var reportsBitRange = regexp.MustCompile(`^[us]\d+\[\d+:\d+\]$`)

// lookupLabels is a lookup's labels, and its `default` where that is not a `${value}` template.
func lookupLabels(lookup any) []any {
	switch l := lookup.(type) {
	case []any:
		return l
	}
	if m := asStringMap(lookup); m != nil {
		keys := make([]string, 0, len(m))
		for k := range m {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		var out []any
		for _, k := range keys {
			if text, ok := m[k].(string); ok && k == "default" && strings.Contains(text, lookupValueToken) {
				continue
			}
			out = append(out, m[k])
		}
		return out
	}
	return nil
}

// jsonKind is the JSON type of a lookup label: boolean, integer, number or string.
func jsonKind(v any) string {
	switch val := v.(type) {
	case bool:
		return "boolean"
	case int, int64, uint64, uint:
		return "integer"
	case float32:
		return jsonKind(float64(val))
	case float64:
		if val == math.Trunc(val) && !math.IsInf(val, 0) {
			return "integer"
		}
		return "number"
	}
	return "string"
}

// reportsOf is PS-570's reports_of: the JSON type PS-279 selects for a field's reported
// value, from the declaration alone. "" where nothing declares one.
func reportsOf(decl map[string]any) string {
	if r, ok := decl["_reports"].(string); ok { // a match discriminator
		return r
	}
	if asStringMap(decl["tlv"]) != nil && !truthy(decl["type"]) {
		return "array" // `channels` of a merge: false tlv
	}
	if lookup, present := decl["lookup"]; present && lookup != nil {
		if m := asStringMap(lookup); m != nil {
			if def, ok := m["default"].(string); ok && strings.Contains(def, lookupValueToken) {
				return "string"
			}
		}
		kinds := map[string]bool{}
		for _, v := range lookupLabels(lookup) {
			kinds[jsonKind(v)] = true
		}
		if len(kinds) == 2 && kinds["integer"] && kinds["number"] {
			return "number"
		}
		if len(kinds) == 1 {
			for k := range kinds {
				return k
			}
		}
		return ""
	}
	if spec := asStringMap(decl["time"]); spec != nil { // PS-610 (CR-2026-186)
		switch spec["format"] {
		case "unix":
			return "number"
		case "calendar":
			return "object"
		}
		return "string"
	}
	declared := ""
	if t, ok := decl["type"]; ok && t != nil {
		declared = fmt.Sprint(t)
	}
	t := strings.ToLower(declared)
	_, hasValue := decl["value"]
	switch {
	case t == "repeat":
		return "array"
	case t == "object":
		return "object"
	case (t == "string" || t == "number" || t == "boolean") && hasValue:
		return t
	case t == "bytes" && decl["format"] == "array":
		return "array"
	case t == "bool" || t == "boolean":
		return "boolean"
	case t == "ascii":
		if truthy(decl["numeral"]) {
			return "integer"
		}
		return "string"
	case t == "string" || t == "bitfield_string" || t == "enum" || t == "bytes" || t == "hex" || t == "base64":
		return "string"
	}
	for _, k := range []string{"mult", "div", "add", "polynomial", "transform"} {
		if _, ok := decl[k]; ok {
			return "number"
		}
	}
	switch {
	case t == "integer":
		return "integer"
	case reportsFloatTypes[t]:
		return "number"
	case reportsBitRange.MatchString(t) || integerTypeNames[t]:
		return "integer"
	}
	return ""
}

// checkLookupTypes is check_lookup_types (PS-571): the first lookup, in source order of the
// header, the fields, the ports and the definitions, whose labels - and a `default` that is
// not a `${value}` template - are not of one JSON type (integers and other numbers are one
// type, number).
func checkLookupTypes(raw map[string]any, order keyOrder) error {
	var walk func(node any) error
	walk = func(node any) error {
		if list, ok := node.([]any); ok {
			for _, item := range list {
				if err := walk(item); err != nil {
					return err
				}
			}
			return nil
		}
		m := asStringMap(node)
		if m == nil {
			return nil
		}
		if lookup, has := m["lookup"]; has && m["type"] != nil {
			kinds := map[string]bool{}
			for _, v := range lookupLabels(lookup) {
				k := jsonKind(v)
				if k == "integer" {
					k = "number"
				}
				kinds[k] = true
			}
			if len(kinds) > 1 {
				names := make([]string, 0, len(kinds))
				for k := range kinds {
					names = append(names, k)
				}
				sort.Strings(names)
				name := "None"
				if s, ok := m["name"].(string); ok {
					name = s
				}
				return fmt.Errorf("Field '%s': its lookup mixes %s labels; a lookup's labels are of one type (PS-571)", name, strings.Join(names, ", "))
			}
		}
		for _, key := range order.keys(node) {
			if key == "test_vectors" {
				continue
			}
			if err := walk(m[key]); err != nil {
				return err
			}
		}
		return nil
	}
	for _, key := range []string{"header", "fields", "ports", "definitions"} {
		if err := walk(raw[key]); err != nil {
			return err
		}
	}
	return nil
}
