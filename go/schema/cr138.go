package schema

// CR-2026-138 (PS-553): a field has a name; padding is a skip.
//
// Mirrors field_name_errors in tools/schema_interpreter.py, which is the spec prototype's
// check_field_names: every field that reads or reports a value has a `name`, except a
// `skip`. A construct (`match`, `byte_group`, `flagged`, `tlv`, `records`) is not a field,
// a byte_group member may leave reserved bits unnamed, and a repeat's `element` is
// nameless (PS-528). Any other nameless field rejects the schema at load, identified by
// its 1-based position in its list. Before, Go read such a field and dropped it, and the
// encoder wrote it as zero padding.

import (
	"fmt"
	"sort"
	"strconv"
)

// namelessConstructs are the construct keys that make an entry with no `type` not a field.
var namelessConstructs = []string{"match", "byte_group", "flagged", "tlv"}

// checkFieldNames walks the header, the fields and each port's fields, through an
// object's or a repeat's `fields` and `trailer` (which add `, in '<name>'` to the
// label), and through match cases and default, flagged groups, tlv cases and `records`
// bodies (which add nothing). It runs on the document after its `$ref`s are spliced.
func checkFieldNames(raw map[string]any) error {
	label := "Schema None"
	if name, ok := raw["name"].(string); ok {
		label = "Schema '" + name + "'"
	}
	if err := fieldNameError(raw["header"], label+", header"); err != nil {
		return err
	}
	if err := fieldNameError(raw["fields"], label); err != nil {
		return err
	}
	ports := asStringMap(raw["ports"])
	keys := make([]string, 0, len(ports))
	for k := range ports {
		keys = append(keys, k)
	}
	sortKeys(keys)
	for _, port := range keys {
		group := ports[port]
		if m := asStringMap(group); m != nil {
			group = m["fields"]
		}
		if err := fieldNameError(group, label+", port "+port); err != nil {
			return err
		}
	}
	return nil
}

// sortKeys orders mapping keys numerically where both are integers, else as text, so the
// first error reported does not vary between runs as Go's map order does.
func sortKeys(keys []string) {
	sort.SliceStable(keys, func(i, j int) bool {
		a, errA := strconv.Atoi(keys[i])
		b, errB := strconv.Atoi(keys[j])
		if errA == nil && errB == nil {
			return a < b
		}
		if (errA == nil) != (errB == nil) {
			return errA == nil
		}
		return keys[i] < keys[j]
	})
}

// caseBodies returns a match's or a tlv's case bodies, in key order.
func caseBodies(cases any) []any {
	m := asStringMap(cases)
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sortKeys(keys)
	out := make([]any, 0, len(keys))
	for _, k := range keys {
		out = append(out, m[k])
	}
	return out
}

func fieldNameError(items any, where string) error {
	list, ok := items.([]any)
	if !ok {
		return nil
	}
	for i, entry := range list {
		position := i + 1
		item := asStringMap(entry)
		if item == nil {
			continue
		}
		_, hasType := item["type"]
		if records, ok := item["records"]; ok {
			bodies, _ := records.([]any)
			for _, body := range bodies {
				if err := fieldNameError(body, where); err != nil {
					return err
				}
			}
			continue
		}
		construct := false
		for _, key := range namelessConstructs {
			if _, ok := item[key]; ok {
				construct = true
			}
		}
		if construct && !hasType {
			if spec := asStringMap(item["match"]); spec != nil {
				for _, body := range append(caseBodies(spec["cases"]), spec["default"]) {
					if err := fieldNameError(body, where); err != nil {
						return err
					}
				}
			}
			for _, key := range []string{"tlv", "flagged"} {
				switch spec := item[key].(type) {
				case []any:
					if err := fieldNameError(spec, where); err != nil {
						return err
					}
				default:
					m := asStringMap(spec)
					if m == nil {
						continue
					}
					if err := fieldNameError(m["fields"], where); err != nil {
						return err
					}
					for _, body := range caseBodies(m["cases"]) {
						if err := fieldNameError(body, where); err != nil {
							return err
						}
					}
					groups, _ := m["groups"].([]any)
					for _, g := range groups {
						if gm := asStringMap(g); gm != nil {
							if err := fieldNameError(gm["fields"], where); err != nil {
								return err
							}
						}
					}
				}
			}
			continue
		}
		typ := item["type"]
		if typ == "skip" {
			continue
		}
		if _, ok := item["object"]; ok && !hasType {
			continue // the withdrawn `object:` key, which PS-466 names
		}
		name, _ := item["name"].(string)
		if name == "" {
			kind := "field"
			if hasType {
				kind = fmt.Sprintf("%v", typ)
			}
			return fmt.Errorf("%s: field %d, a %s, has no name; a field that reads or "+
				"reports a value is named, and padding is a `skip` (PS-553)", where, position, kind)
		}
		inner := where + ", in '" + name + "'"
		if err := fieldNameError(item["fields"], inner); err != nil {
			return err
		}
		if err := fieldNameError(item["trailer"], inner); err != nil {
			return err
		}
	}
	return nil
}
