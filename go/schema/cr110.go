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
	"sort"
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

// onRepeatOpaque are the mappings whose keys are data, not schema, and `definitions`,
// which is held to the rules where a `$ref` splices it.
var onRepeatOpaque = map[string]bool{
	"lookup": true, "values": true, "test_vectors": true, "metadata": true, "definitions": true,
}

// checkOnRepeat is on_repeat_schema_errors: every tlv in the schema is held to PS-525
// and PS-526 at load, whether or not a payload reaches it, and an `on_repeat` declared
// where it has no effect is refused (PS-525, CR-2026-125): it belongs on a tlv, on a
// field a tlv case reports, and as `last` on a member of a merged object.
func checkOnRepeat(raw map[string]any, order keyOrder) error {
	// Declarations are compared by identity, a map's address. A field mapping has string
	// keys, so yaml.v3 hands it over as map[string]any and asStringMap returns it as is.
	allowedSet := map[uintptr]bool{}
	var firstErr error
	var tlvs func(node any)
	tlvs = func(node any) {
		if m := asStringMap(node); m != nil {
			if tlv := asStringMap(m["tlv"]); tlv != nil {
				allowedSet[identity(tlv)] = true
				info, err := tlvRepeatModes(tlv, order)
				if err != nil && firstErr == nil {
					firstErr = err
				}
				if view := asStringMap(tlv["cases"]); view != nil {
					for _, k := range order.keys(tlv["cases"]) {
						body, ok := view[k].([]any)
						if !ok {
							continue
						}
						names, found := reportedFields(body, order)
						for _, name := range names {
							allowedSet[identity(found[name])] = true
							if info.modes[name] != "merge" {
								continue
							}
							members, _ := found[name]["fields"].([]any)
							for _, member := range members {
								if mm := asStringMap(member); mm != nil && mm["on_repeat"] == "last" {
									allowedSet[identity(mm)] = true
								}
							}
						}
					}
				}
			}
			for _, k := range order.keys(node) {
				if !onRepeatOpaque[k] {
					tlvs(m[k])
				}
			}
		} else if list, ok := node.([]any); ok {
			for _, item := range list {
				tlvs(item)
			}
		}
	}
	var strays func(node any)
	strays = func(node any) {
		if firstErr != nil {
			return
		}
		if m := asStringMap(node); m != nil {
			if _, declared := m["on_repeat"]; declared && !allowedSet[identity(m)] {
				name, ok := m["name"].(string)
				if !ok {
					name = "an entry"
				}
				firstErr = fmt.Errorf("`on_repeat` on '%s' has no effect there: it belongs on a tlv, a field of a tlv case, or a merged object's member (PS-525)", name)
				return
			}
			for _, k := range order.keys(node) {
				if !onRepeatOpaque[k] {
					strays(m[k])
				}
			}
		} else if list, ok := node.([]any); ok {
			for _, item := range list {
				strays(item)
			}
		}
	}
	tlvs(raw)
	strays(raw)
	return firstErr
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

// identity is a map's address, so a declaration is recognised as the same mapping.
func identity(m map[string]any) uintptr {
	id, _ := mapIdentity(m)
	return id
}

// tlvEncodePlan is tlv_encode_plan: what PS-527 (CR-2026-125) needs of a tlv's cases,
// in declaration order. first is the first case declaring each name - a `last` value
// and an array PS-157 collected are written under it. For each merged object, members
// is every member some case declares, owner the first case declaring each member,
// which writes it once, and declaring every case declaring each member, all of which
// write a member that declares `last`.
type encodePlan struct {
	first     map[string]string
	members   map[string]map[string]bool
	owner     map[string]map[string]string
	declaring map[string]map[string]map[string]bool
	last      map[string]map[string]bool
}

func tlvEncodePlan(tlv map[string]any, order keyOrder, info tlvRepeatInfo) encodePlan {
	plan := encodePlan{
		first:     map[string]string{},
		members:   map[string]map[string]bool{},
		owner:     map[string]map[string]string{},
		declaring: map[string]map[string]map[string]bool{},
		last:      info.lastMembers,
	}
	view := asStringMap(tlv["cases"])
	if view == nil {
		return plan
	}
	for _, key := range order.keys(tlv["cases"]) {
		body, ok := view[key].([]any)
		if !ok || key == "default" {
			continue
		}
		names, found := reportedFields(body, order)
		for _, name := range names {
			if _, seen := plan.first[name]; !seen {
				plan.first[name] = key
			}
			if info.mode(name) != "merge" {
				continue
			}
			members, _ := found[name]["fields"].([]any)
			for _, member := range members {
				mm := asStringMap(member)
				m, _ := mm["name"].(string)
				if mm == nil || m == "" {
					continue
				}
				if plan.members[name] == nil {
					plan.members[name] = map[string]bool{}
					plan.owner[name] = map[string]string{}
					plan.declaring[name] = map[string]map[string]bool{}
				}
				plan.members[name][m] = true
				if _, owned := plan.owner[name][m]; !owned {
					plan.owner[name][m] = key
				}
				if plan.declaring[name][m] == nil {
					plan.declaring[name][m] = map[string]bool{}
				}
				plan.declaring[name][m][key] = true
			}
		}
	}
	return plan
}

// writes is whether the case under key writes a merged object's member: the first case
// declaring it, or - for a member declaring `last` - every case declaring it (PS-527).
func (p encodePlan) writes(name, member, key string) bool {
	if p.owner[name][member] == key {
		return true
	}
	return p.last[name][member] && p.declaring[name][member][key]
}

// strayMember is PS-527's refusal of a supplied member of a merged object that no case
// declares, in the reference's wording.
func (p encodePlan) strayMember(data map[string]any) error {
	names := make([]string, 0, len(p.members))
	for name := range p.members {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		obj, ok := data[name].(map[string]any)
		if !ok {
			continue
		}
		stray := []string{}
		for m := range obj {
			if !p.members[name][m] {
				stray = append(stray, "'"+m+"'")
			}
		}
		if len(stray) > 0 {
			sort.Strings(stray)
			return fmt.Errorf("tlv: the merged object '%s' is given [%s], which no case declares (PS-527)",
				name, strings.Join(stray, ", "))
		}
	}
	return nil
}

// splitCollected is PS-502's one entry per element: every name the case writes must be
// an array PS-157 collected, all of one length, or the elements cannot be paired.
func splitCollected(key string, claimed, arrays []string, data map[string]any) ([]map[string]any, error) {
	length := -1
	same := len(arrays) == len(claimed)
	for _, name := range arrays {
		n := len(data[name].([]any))
		if length >= 0 && n != length {
			same = false
		}
		length = n
	}
	if !same {
		quoted := make([]string, len(claimed))
		for i, name := range claimed {
			quoted[i] = "'" + name + "'"
		}
		return nil, fmt.Errorf("tlv case %s: [%s] cannot be written one entry per element: the collected arrays differ in length or sit beside a single value (PS-502)",
			key, strings.Join(quoted, ", "))
	}
	entries := make([]map[string]any, length)
	for i := range entries {
		entry := make(map[string]any, len(data))
		for k, v := range data {
			entry[k] = v
		}
		for _, name := range arrays {
			entry[name] = data[name].([]any)[i]
		}
		entries[i] = entry
	}
	return entries, nil
}
