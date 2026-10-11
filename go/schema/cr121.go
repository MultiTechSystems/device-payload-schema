package schema

// CR-2026-121, CR-2026-123 and CR-2026-126: element repeats completed.
//
// PS-544 (a null element is written as the element's first sentinel), PS-545 (an element
// is a data field; an `_` name hides nothing), PS-546 and PS-547 (`$name[i]`, a sibling
// repeat identifying values position by position, and its `_meta` listing), and PS-548
// (an `ipso.instance` is an integer from 0 to 65534). Each function mirrors the one of
// the same name in tools/schema_interpreter.py: sibling_references, sibling_errors,
// element_repeats and ipso_instance_errors.

import (
	"fmt"
	"regexp"
)

// elementConstructs are what an `element` may not be (PS-545).
var elementConstructs = []string{"match", "byte_group", "flagged", "tlv", "records"}

// siblingReference is `$name[i]`: the value a sibling repeat of single values reported
// at this position (PS-546).
var siblingReference = regexp.MustCompile(`^\$(\w+)\[(\w+)\]$`)

// ipsoInstanceMax is the largest LwM2M object instance ID; 65535 is reserved (PS-548).
const ipsoInstanceMax = 65534

type siblingRef struct{ key, name, index string }

// siblingReferences is sibling_references: the `$name[i]` references a repeat declares -
// its identity, and its elements' `unit` and `ipso.instance` - in that order.
func siblingReferences(repeat map[string]any) []siblingRef {
	type candidate struct {
		key   string
		value any
	}
	refs := []candidate{{"identity", repeat["identity"]}}
	members, _ := rawRepeatMembers(repeat)
	for _, raw := range members {
		if member := asStringMap(raw); member != nil {
			var instance any
			if ipso := asStringMap(member["ipso"]); ipso != nil {
				instance = ipso["instance"]
			}
			refs = append(refs, candidate{"unit", member["unit"]},
				candidate{"ipso.instance", instance})
		}
	}
	var found []siblingRef
	for _, c := range refs {
		text, ok := c.value.(string)
		if !ok {
			continue
		}
		if m := siblingReference.FindStringSubmatch(text); m != nil {
			found = append(found, siblingRef{c.key, m[1], m[2]})
		}
	}
	return found
}

// siblingNames are the distinct sibling names a repeat's references name, in order.
func siblingNames(repeat map[string]any) []string {
	var names []string
	seen := map[string]bool{}
	for _, r := range siblingReferences(repeat) {
		if !seen[r.name] {
			seen[r.name] = true
			names = append(names, r.name)
		}
	}
	return names
}

func isSiblingReference(v any) bool {
	text, ok := v.(string)
	return ok && siblingReference.MatchString(text)
}

// siblingError is sibling_errors: each `$name[i]` names a repeat declaring `element`,
// decoded earlier in the same field list (earlier, by name), and `i` is this repeat's
// `index`; the sibling's element carries a lookup or is a literal (PS-374).
func siblingError(repeat map[string]any, earlier map[string]map[string]any, at string) error {
	index, _ := repeat["index"].(string)
	for _, r := range siblingReferences(repeat) {
		label := fmt.Sprintf("%s: %s `$%s[%s]`", at, r.key, r.name, r.index)
		if r.index != index {
			return fmt.Errorf("%s must index by the repeat's own `index` (PS-546)", label)
		}
		sibling, found := earlier[r.name]
		if !found {
			return fmt.Errorf("%s names no repeat declaring `element` earlier in the same field list (PS-546)", label)
		}
		element := asStringMap(sibling["element"])
		if _, hasLookup := element["lookup"]; !hasLookup && !isRawLiteral(element) {
			return fmt.Errorf("%s: the elements of '%s' identify values but carry no lookup and are not a literal (PS-374, PS-546)",
				label, r.name)
		}
	}
	return nil
}

// elementRepeats is element_repeats: name -> declaration of each repeat declaring
// `element` in one field list, the sibling a `$name[i]` can name (PS-546). A construct
// that merges into its parent (`match`, `tlv`, `flagged`) is part of the list; an object
// or repeat is not.
func elementRepeats(fields any) map[string]map[string]any {
	found := map[string]map[string]any{}
	var visit func(items any)
	visit = func(items any) {
		list, _ := items.([]any)
		for _, raw := range list {
			f := asStringMap(raw)
			if f == nil {
				continue
			}
			if f["type"] == "repeat" {
				name, _ := f["name"].(string)
				if asStringMap(f["element"]) != nil && name != "" {
					if _, seen := found[name]; !seen {
						found[name] = f
					}
				}
				continue
			}
			for _, construct := range []string{"match", "tlv"} {
				if body := asStringMap(f[construct]); body != nil {
					for _, c := range asStringMap(body["cases"]) {
						visit(c)
					}
					visit(body["default"])
				}
			}
			if flagged := asStringMap(f["flagged"]); flagged != nil {
				groups, _ := flagged["groups"].([]any)
				for _, g := range groups {
					if gm := asStringMap(g); gm != nil {
						visit(gm["fields"])
					}
				}
			}
		}
	}
	visit(fields)
	return found
}

// siblingListing is PS-547: a `$name[i]` listed as {repeat, values[, open]} from the
// sibling's element, as PS-375 and PS-489 list a field.
func (s *Schema) siblingListing(name string, siblings map[string]map[string]any) map[string]any {
	element := map[string]any{}
	if target := siblings[name]; target != nil {
		for k, v := range asStringMap(target["element"]) {
			element[k] = v
		}
		element["name"] = elementName
	}
	listing, _ := s.metaReference("$"+elementName, map[string]any{"element": element}, false, nil).(map[string]any)
	out := map[string]any{"repeat": name, "values": listing["values"]}
	if open, present := listing["open"]; present {
		out["open"] = open
	}
	return out
}

// ipsoInstanceError is ipso_instance_errors: an `ipso.instance` is an integer from 0 to
// 65534, or a reference every value of which is one - a repeat's index whose range from
// `index_base` fits, a literal such integer, or a lookup whose labels are all such
// integers with no `${value}` default (PS-548). Checked through the listing `_meta`
// would carry, so own-element and sibling references are held to the same rule.
func ipsoInstanceError(raw map[string]any) error {
	valid := func(v any) bool {
		if _, isBool := v.(bool); isBool {
			return false
		}
		n, ok := intValue(v)
		return ok && n >= 0 && n <= ipsoInstanceMax
	}
	s := &Schema{}
	check := func(field, repeat map[string]any, siblings map[string]map[string]any) error {
		instance := asStringMap(field["ipso"])["instance"]
		listing := s.metaReference(instance, repeat, false, siblings)
		ok := true
		if lm, isMap := listing.(map[string]any); isMap {
			if _, isIndex := lm["index"]; isIndex {
				count, literal := intValue(lm["count"])
				if !literal {
					return nil // PS-374 reports an index with no literal count or max
				}
				low, _ := intValue(lm["index_base"])
				if count < 1 {
					count = 1
				}
				ok = valid(low) && valid(low+count-1)
			} else {
				_, open := lm["open"]
				ok = !open
				values, _ := lm["values"].([]any)
				for _, v := range values {
					ok = ok && valid(v)
				}
			}
		} else {
			ok = valid(listing)
		}
		if !ok {
			name, _ := field["name"].(string)
			if name == "" {
				name = "<unnamed>"
			}
			return fmt.Errorf("Field '%s': its ipso.instance can be other than an integer from 0 to %d (PS-548)",
				name, ipsoInstanceMax)
		}
		return nil
	}
	skip := map[string]bool{"test_vectors": true, "definitions": true, "ipso": true, "senml": true, "lookup": true}
	var walk func(node any, repeat map[string]any, siblings map[string]map[string]any) error
	var walkItem func(item any, repeat map[string]any, siblings, here map[string]map[string]any) error
	walk = func(node any, repeat map[string]any, siblings map[string]map[string]any) error {
		list, ok := node.([]any)
		if !ok {
			return nil
		}
		here := elementRepeats(list)
		for _, item := range list {
			if err := walkItem(item, repeat, siblings, here); err != nil {
				return err
			}
		}
		return nil
	}
	walkItem = func(item any, repeat map[string]any, siblings, here map[string]map[string]any) error {
		m := asStringMap(item)
		if m == nil {
			return walk(item, repeat, siblings)
		}
		if ipso := asStringMap(m["ipso"]); ipso != nil {
			if _, present := ipso["instance"]; present {
				if err := check(m, repeat, siblings); err != nil {
					return err
				}
			}
		}
		for key, value := range m {
			if skip[key] {
				continue
			}
			if m["type"] == "repeat" && (key == "fields" || key == "element") {
				// A member's references name this repeat's index and fields, and the
				// element repeats beside it (PS-546).
				members := value
				if _, isList := value.([]any); !isList {
					members = []any{value}
				}
				if err := walk(members, m, here); err != nil {
					return err
				}
			} else if asStringMap(value) != nil {
				if err := walkItem(value, repeat, siblings, here); err != nil {
					return err
				}
			} else if err := walk(value, repeat, siblings); err != nil {
				return err
			}
		}
		return nil
	}
	return walkItem(raw, nil, map[string]map[string]any{}, map[string]map[string]any{})
}
