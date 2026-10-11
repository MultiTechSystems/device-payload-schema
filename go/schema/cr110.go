package schema

// CR-2026-110: `on_repeat` combines a name reported by more than one tlv entry, whether
// the entries share a tag or not (PS-157, PS-525 to PS-527). Each function mirrors the
// one of the same name in tools/schema_interpreter.py: reported_fields,
// tlv_repeat_modes, on_repeat_schema_errors and combine_repeated. The messages are the
// spec prototype's.
//
// The rules read the tlv's declaration as written (Field.decl), not the typed model:
// `on_repeat` belongs to a case's field and to the tlv, and the cases' declaration order
// - which decides the PS-526 message and the case PS-527 writes a `last` value under -
// is recovered from the YAML node tree (keyOrder), since a Go map has none.

import (
	"fmt"
	"strings"
)

// onRepeatModes is the closed set PS-525 allows, in the order the messages list them.
var onRepeatModes = []string{"collect", "merge", "last"}

func isOnRepeatMode(v any) bool {
	s, ok := v.(string)
	if !ok {
		return false
	}
	for _, m := range onRepeatModes {
		if s == m {
			return true
		}
	}
	return false
}

// reportingConstructs are the constructs whose fields report into the enclosing object.
var reportingConstructs = []string{"match", "byte_group", "flagged", "tlv"}

// reportedFields is every named field a raw field list reports, first declaration
// first, looking through the constructs whose fields merge into the enclosing object.
// An object's or repeat's members are not descended into.
func reportedFields(fields any, order keyOrder) ([]string, map[string]map[string]any) {
	var names []string
	found := map[string]map[string]any{}
	var walk func(items any)
	walk = func(items any) {
		list, ok := items.([]any)
		if !ok {
			return
		}
		for _, item := range list {
			f := asStringMap(item)
			if f == nil {
				continue
			}
			if _, typed := f["type"]; typed {
				if name, _ := f["name"].(string); name != "" {
					if _, seen := found[name]; !seen {
						found[name] = f
						names = append(names, name)
					}
				}
				walk(f["trailer"])
				continue
			}
			var spec any
			for _, key := range reportingConstructs {
				if v, ok := f[key]; ok {
					spec = v
					break
				}
			}
			if _, isList := spec.([]any); isList {
				walk(spec)
			} else if m := asStringMap(spec); m != nil {
				walk(m["fields"])
				if cases, ok := m["cases"]; ok && asStringMap(cases) != nil {
					view := asStringMap(cases)
					for _, key := range order.keys(cases) {
						walk(view[key])
					}
				}
				if groups, ok := m["groups"].([]any); ok {
					for _, g := range groups {
						if gm := asStringMap(g); gm != nil {
							walk(gm["fields"])
						}
					}
				}
			}
		}
	}
	walk(fields)
	return names, found
}

// tlvRepeatInfo is how each name a tlv reports combines when more than one entry
// reports it (PS-525), and the members of each merged object that declare `last`.
type tlvRepeatInfo struct {
	modes       map[string]string
	lastMembers map[string]map[string]bool
	// lastOwner is, for each `last` name, the first case in declaration order that
	// declares it: the one case PS-527 writes it under.
	lastOwner map[string]string
}

func (i tlvRepeatInfo) mode(name string) string {
	if m, ok := i.modes[name]; ok {
		return m
	}
	return "collect"
}

// tlvRepeatModes resolves every case field's mode (PS-525) and reports the first
// violation of PS-525/PS-526. A field's own `on_repeat` takes precedence; a tlv's
// `merge` reaches its `type: object` fields only, and any other field keeps `collect`;
// with no `on_repeat` anywhere every field collects.
func tlvRepeatModes(tlv map[string]any, order keyOrder) (tlvRepeatInfo, error) {
	info := tlvRepeatInfo{
		modes:       map[string]string{},
		lastMembers: map[string]map[string]bool{},
		lastOwner:   map[string]string{},
	}
	if tlv == nil {
		return info, nil
	}
	var firstErr error
	fail := func(format string, args ...any) {
		if firstErr == nil {
			firstErr = fmt.Errorf(format, args...)
		}
	}
	def := "collect"
	if v, ok := tlv["on_repeat"]; ok {
		if isOnRepeatMode(v) {
			def = v.(string)
		} else {
			fail("tlv `on_repeat` is one of %s, not %s (PS-525)",
				strings.Join(onRepeatModes, ", "), pyRepr(v))
		}
	}
	cases, ok := tlv["cases"]
	view := asStringMap(cases)
	if !ok || view == nil {
		return info, firstErr
	}
	for _, key := range order.keys(cases) {
		body, isList := view[key].([]any)
		if !isList {
			continue
		}
		names, found := reportedFields(body, order)
		for _, name := range names {
			fd := found[name]
			own, declared := fd["on_repeat"]
			if declared && !isOnRepeatMode(own) {
				fail("Field '%s': `on_repeat` is one of %s, not %s (PS-525)",
					name, strings.Join(onRepeatModes, ", "), pyRepr(own))
				continue
			}
			typ, _ := fd["type"].(string)
			isObject := typ == "object"
			if declared && own == "merge" && !isObject {
				fail("Field '%s': only a `type: object` field may declare `on_repeat: merge` (PS-526)", name)
				continue
			}
			mode := def
			if declared {
				mode = own.(string)
			} else if def == "merge" && !isObject {
				mode = "collect"
			}
			if prior, seen := info.modes[name]; seen {
				if prior != mode {
					fail("tlv: the fields reporting '%s' declare different `on_repeat`, %s and %s (PS-526)",
						name, prior, mode)
				}
				continue
			}
			info.modes[name] = mode
			if mode == "last" {
				info.lastOwner[name] = key
			}
		}
		for _, name := range names {
			if info.modes[name] != "merge" {
				continue
			}
			members, _ := found[name]["fields"].([]any)
			for _, m := range members {
				mm := asStringMap(m)
				if mm == nil || mm["on_repeat"] != "last" {
					continue
				}
				if member, _ := mm["name"].(string); member != "" {
					if info.lastMembers[name] == nil {
						info.lastMembers[name] = map[string]bool{}
					}
					info.lastMembers[name][member] = true
				}
			}
		}
	}
	return info, firstErr
}

// checkOnRepeat is on_repeat_schema_errors: every tlv in the schema is held to PS-525
// and PS-526 at load, whether or not a payload reaches it.
func checkOnRepeat(raw map[string]any, order keyOrder) error {
	var walk func(node any) error
	walk = func(node any) error {
		if m := asStringMap(node); m != nil {
			if tlv := asStringMap(m["tlv"]); tlv != nil {
				if _, err := tlvRepeatModes(tlv, order); err != nil {
					return err
				}
			}
			for key, value := range m {
				if key == "test_vectors" {
					continue
				}
				if err := walk(value); err != nil {
					return err
				}
			}
		} else if list, ok := node.([]any); ok {
			for _, item := range list {
				if err := walk(item); err != nil {
					return err
				}
			}
		}
		return nil
	}
	return walk(raw)
}

// combineRepeated is PS-525: `last` keeps the later value; `merge` combines two
// objects member by member, and a member reported twice is an error unless it declares
// `on_repeat: last`.
func combineRepeated(name string, earlier, value any, mode string, last map[string]bool) (any, error) {
	if mode == "last" {
		return value, nil
	}
	before, okA := earlier.(map[string]any)
	after, okB := value.(map[string]any)
	if !okA || !okB {
		return value, nil
	}
	combined := make(map[string]any, len(before)+len(after))
	for k, v := range before {
		combined[k] = v
	}
	for k, v := range after {
		if _, twice := combined[k]; twice && !last[k] {
			return nil, fmt.Errorf("tlv: the merged object '%s' reports its member '%s' twice (PS-525)", name, k)
		}
		combined[k] = v
	}
	return combined, nil
}

// reportsList is _reports_list: whether a field's own value is a list - a repeat, or
// bytes rendered as an array - so a list under its name is not PS-157's collection.
func reportsList(f Field) bool {
	switch f.Type {
	case TypeRepeat, TypeRepeatLower:
		return true
	case TypeBytes, TypeBytesLower:
		return f.Format == "array"
	}
	return false
}
