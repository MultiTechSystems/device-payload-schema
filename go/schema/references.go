package schema

import (
	"fmt"
	"regexp"
	"strings"
)

// PS-539 (CR-2026-117, CR-2026-127): every `$` reference names something the schema
// declares - a field, a `var`, a tag component, a repeat's `index` or `count_as` - and the
// declaration comes before the reference in schema order (PS-122, PS-147): a tlv's tag
// components before its cases, a repeat's `trailer` and elements before its `present_if`
// and `identity`. The document is walked with every `$ref` already spliced, so a
// definition's references are checked where it is used, after the fields declared before
// the splice, and an unused definition is never checked. A nameless repeat element
// declares no name (PS-528). `metadata` include and timestamp names count as declared
// first; a `metadata.include` `source` names runtime input (PS-315) and is not checked.
// Whether a name declared earlier is bound when it is read is a decode matter (PS-314).
//
// The walk is the spec prototype's `key_vocabulary.check_references` and
// reference_errors in the reference implementation; Java and C# walk the same way and all
// four give the messages identically. Every mapping, the ports included, is walked in
// source order (referenceWalkOrder).

// referenceKeys are the keys whose string value may be a `$` reference.
var referenceKeys = map[string]bool{"ref": true, "a": true, "b": true, "field": true,
	"count": true, "byte_length": true, "carry": true, "instance": true, "unit": true,
	"identity": true}

// referenceDataKeys hold data, not declarations or references. A `default` is data too,
// except a match's, which is a field list.
var referenceDataKeys = map[string]bool{"lookup": true, "values": true, "else": true,
	"value": true}

// referenceFirstKeys are walked before the rest of their mapping, because what the rest
// reads comes after them.
var referenceFirstKeys = []string{"tag_fields", "trailer", "fields", "element"}

var declaredNameTemplate = regexp.MustCompile(`\$\{(\w+)\}`)

// referenceErrorMessage is PS-539's wording, the spec prototype's.
func referenceErrorMessage(name, key string) string {
	return fmt.Sprintf("`$%s` (%s) names no field, var, tag component, index or count_as "+
		"the schema declares (PS-539)", name, key)
}

// referenceOrderMessage is PS-539's ordering rule (CR-2026-127), the prototype's wording.
func referenceOrderMessage(name, key string) string {
	return fmt.Sprintf("`$%s` (%s) is referenced before the schema declares it (PS-539, PS-122)",
		name, key)
}

type schemaReference struct{ name, key string }

// referenceWalkOrder is the order a mapping's keys are walked in, PS-539's decode order:
// referenceFirstKeys first, in that order, then the rest in source order. A Go map keeps
// no order, so the source order comes from the YAML node tree (buildKeyOrder), as _meta
// takes a lookup's labels; a mapping with none recorded falls back to keyOrder.keys's
// sorted order.
func referenceWalkOrder(node any, order keyOrder) []string {
	m := asStringMap(node)
	var out []string
	for _, k := range referenceFirstKeys {
		if _, ok := m[k]; ok {
			out = append(out, k)
		}
	}
	for _, k := range order.keys(node) {
		if !referenceFirstKey(k) {
			out = append(out, k)
		}
	}
	return out
}

func referenceFirstKey(k string) bool {
	for _, f := range referenceFirstKeys {
		if f == k {
			return true
		}
	}
	return false
}

// checkReferences rejects the first reference that names nothing, or that precedes every
// declaration of its name. `raw` is the document after expandRawRefs, and `order` its
// source key order.
func checkReferences(raw map[string]any, order keyOrder) error {
	declared := map[string]bool{}
	var ahead []schemaReference

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
		}
		for _, k := range referenceWalkOrder(node, order) {
			v := m[k]
			if referenceDataKeys[k] {
				continue
			}
			if s, ok := v.(string); ok {
				var names []string
				if referenceKeys[k] && strings.HasPrefix(s, "$") {
					names = []string{s[1:]}
				} else if k == "name_from" || (inSenml && k == "name") {
					for _, match := range declaredNameTemplate.FindAllStringSubmatch(s, -1) {
						names = append(names, match[1])
					}
				}
				for _, n := range names {
					if !declared[n] {
						ahead = append(ahead, schemaReference{n, k})
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
	walk(raw["header"], "", false)
	walk(raw["fields"], "", false)
	// Each port is a decode of its own (CR-2026-130): what one port declares is never bound
	// while another is decoded, so each starts from what the top level declares.
	shared := map[string]bool{}
	seen := map[string]bool{}
	for n := range declared {
		shared[n], seen[n] = true, true
	}
	if ports := asStringMap(raw["ports"]); ports != nil {
		for _, k := range order.keys(raw["ports"]) {
			declared = map[string]bool{}
			for n := range shared {
				declared[n] = true
			}
			walk(ports[k], "", false)
			for n := range declared {
				seen[n] = true
			}
		}
	}
	declared = seen // to tell "never declared" from "declared too late"
	for _, r := range ahead {
		if !declared[r.name] {
			return fmt.Errorf("%s", referenceErrorMessage(r.name, r.key))
		}
		return fmt.Errorf("%s", referenceOrderMessage(r.name, r.key))
	}
	return nil
}
