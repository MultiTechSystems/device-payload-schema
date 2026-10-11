package schema

import (
	"fmt"
	"regexp"
	"strings"
)

// PS-539 (CR-2026-117): every `$` reference names something the schema declares - a
// field, a `var`, a tag component, a repeat's `index` or `count_as` - in the fields, a
// port or a definition, at any depth. A `metadata` include or timestamp name counts as
// declared; a `metadata.include` `source` names runtime input (PS-315) and is not
// checked. Whether the name is bound when it is read is a decode matter (PS-314), so a
// name declared later, or in a branch not taken, is valid here.
//
// The walk is the spec prototype's `key_vocabulary.check_references`, and
// reference_errors in the reference implementation; Java and C# walk the same way and
// give the message identically.

// referenceKeys are the keys whose string value may be a `$` reference.
var referenceKeys = map[string]bool{"ref": true, "a": true, "b": true, "field": true,
	"count": true, "byte_length": true, "carry": true, "instance": true, "unit": true,
	"identity": true}

// referenceDataKeys hold data, not declarations or references. A `default` is data too,
// except a match's, which is a field list.
var referenceDataKeys = map[string]bool{"lookup": true, "values": true, "else": true,
	"value": true}

var declaredNameTemplate = regexp.MustCompile(`\$\{(\w+)\}`)

// referenceErrorMessage is PS-539's wording, the spec prototype's.
func referenceErrorMessage(name, key string) string {
	return fmt.Sprintf("`$%s` (%s) names no field, var, tag component, index or count_as "+
		"the schema declares (PS-539)", name, key)
}

type schemaReference struct{ name, key string }

// checkReferences rejects the first reference that names nothing, walking each mapping
// in sorted key order.
func checkReferences(raw map[string]any) error {
	declared := map[string]bool{}
	var references []schemaReference

	var walk func(node any, key string, inSenml bool)
	walk = func(node any, key string, inSenml bool) {
		if list, ok := node.([]any); ok {
			for _, item := range list {
				walk(item, "", false)
			}
			return
		}
		m := asStringMap(node)
		if m == nil {
			return
		}
		if !inSenml {
			for _, nameKey := range []string{"name", "var", "index", "count_as"} {
				if s, ok := m[nameKey].(string); ok {
					declared[s] = true
				}
			}
			// An `element` that declares no name is decoded as `element` (PS-528), so its
			// `present_if` and `name_from` can name it.
			if element := asStringMap(m["element"]); element != nil {
				if _, named := element["name"].(string); !named {
					declared["element"] = true
				}
			}
		}
		for _, k := range sortedKeys(m) { // a Go map keeps no source order
			v := m[k]
			if referenceDataKeys[k] {
				continue
			}
			if s, ok := v.(string); ok {
				if referenceKeys[k] && strings.HasPrefix(s, "$") {
					references = append(references, schemaReference{s[1:], k})
				} else if k == "name_from" || (inSenml && k == "name") {
					for _, match := range declaredNameTemplate.FindAllStringSubmatch(s, -1) {
						references = append(references, schemaReference{match[1], k})
					}
				}
			} else if k == "default" {
				if key == "match" {
					switch v.(type) {
					case []any, map[string]any, map[any]any:
						walk(v, "", false) // a match's default is a field list
					}
				}
			} else {
				walk(v, k, k == "senml")
			}
		}
	}

	walk(raw["header"], "", false)
	walk(raw["fields"], "", false)
	for _, part := range []string{"ports", "definitions"} {
		if m := asStringMap(raw[part]); m != nil {
			for _, k := range sortedKeys(m) {
				walk(m[k], "", false)
			}
		}
	}
	if metadata := asStringMap(raw["metadata"]); metadata != nil {
		for _, part := range []string{"include", "timestamps"} {
			if list, ok := metadata[part].([]any); ok {
				for _, entry := range list {
					if name, ok := asStringMap(entry)["name"].(string); ok {
						declared[name] = true
					}
				}
			}
		}
	}
	for _, r := range references {
		if !declared[r.name] {
			return fmt.Errorf("%s", referenceErrorMessage(r.name, r.key))
		}
	}
	return nil
}
