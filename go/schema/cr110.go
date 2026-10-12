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
	"math"
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
	shared := map[string][]map[string]any{}
	var sharedOrder []string
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
				if mm == nil {
					continue
				}
				member, _ := mm["name"].(string)
				if member == "" {
					continue
				}
				key := name + "\x00" + member
				if _, seen := shared[key]; !seen {
					sharedOrder = append(sharedOrder, key)
				}
				shared[key] = append(shared[key], mm)
				if mm["on_repeat"] != "last" {
					continue
				}
				if info.lastMembers[name] == nil {
					info.lastMembers[name] = map[string]bool{}
				}
				info.lastMembers[name][member] = true
			}
		}
	}
	// CR-2026-128: merged cases share a member only where it declares `last` (PS-526).
	for _, key := range sharedOrder {
		decls := shared[key]
		if len(decls) < 2 {
			continue
		}
		for _, d := range decls {
			if d["on_repeat"] != "last" {
				parts := strings.SplitN(key, "\x00", 2)
				fail("tlv: cases share the member '%s' of the merged object '%s' without `on_repeat: last` (PS-526)",
					parts[1], parts[0])
				break
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

// tlvEncodePlan is tlv_encode_plan: for each merged object, every member some case
// declares; a supplied member outside it is an error (PS-527).
type encodePlan struct {
	members map[string]map[string]bool
}

func tlvEncodePlan(tlv map[string]any, order keyOrder, info tlvRepeatInfo) encodePlan {
	plan := encodePlan{members: map[string]map[string]bool{}}
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
			if info.mode(name) != "merge" {
				continue
			}
			members, _ := found[name]["fields"].([]any)
			for _, member := range members {
				mm := asStringMap(member)
				if m, _ := mm["name"].(string); mm != nil && m != "" {
					if plan.members[name] == nil {
						plan.members[name] = map[string]bool{}
					}
					plan.members[name][m] = true
				}
			}
		}
	}
	return plan
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

// caseClaim is a claimable field of a tlv case, and whether the case may be written
// without it: a flagged group's fields, whose mask says which are present, and a field
// declaring `optional: true` (PS-405). The reference's _case_claims.
type caseClaim struct {
	field    Field
	optional bool
}

func caseClaims(fields []Field, optional bool, out []caseClaim) []caseClaim {
	covered := coveredPeeks(fields)
	for i, f := range fields {
		if len(f.ByteGroup) > 0 {
			out = caseClaims(f.ByteGroup, optional, out)
			continue
		}
		if f.Flagged != nil {
			for _, g := range f.Flagged.Groups {
				out = caseClaims(g.Fields, true, out)
			}
			continue
		}
		if f.Name == "" || strings.HasPrefix(f.Name, "_") ||
			f.Type == TypeNumber || f.Type == "number" {
			continue
		}
		// PS-551 (CR-2026-141): a peek the case's next field writes over is derived where
		// the input omits it, so the case does not need it given.
		out = append(out, caseClaim{f, optional || f.Optional || covered[i]})
	}
	return out
}

// tlvCaseOrder is a tlv's case keys in declaration order, recovered from the YAML node
// tree; a key the tree does not give (a JSON-authored schema) follows, sorted.
func tlvCaseOrder(field Field, order keyOrder) []string {
	var keys []string
	seen := map[string]bool{}
	for _, key := range order.keys(field.decl["cases"]) {
		if _, ok := field.TLVCases[key]; ok && !seen[key] {
			keys = append(keys, key)
			seen[key] = true
		}
	}
	var rest []string
	for key := range field.TLVCases {
		if !seen[key] {
			rest = append(rest, key)
		}
	}
	sort.Strings(rest)
	return append(keys, rest...)
}

// fieldTakes is whether a case's field can write a value, the reference's _case_fidelity
// for one field: a label its lookup lacks, a number its arithmetic cannot undo, or one
// outside its type's range is not taken. A value that is not a number after the lookup -
// a string, a boolean, a list - is taken, as the reference takes it.
func fieldTakes(f Field, value any) bool {
	reversed, err := reverseLookupLabel(f, value)
	if err != nil {
		return false
	}
	if _, isBool := reversed.(bool); isBool {
		return true
	}
	num, ok := toFloat64(reversed)
	if !ok {
		return true
	}
	raw, err := reverseArithmetic(num, f)
	if err != nil || math.Abs(raw-math.Round(raw)) > 1e-9 {
		return false
	}
	if lo, hi, known := integerRange(f.Type); known {
		r := int64(math.Round(raw))
		if r < lo || r > hi {
			return false
		}
	}
	return true
}

// claimWidth is how many fields a case's claims count for when ranking cases (PS-527 as
// CR-2026-129 states it): an object counts its members, so a merged {adr, dr} case
// outranks a {dr} one, and anything else counts one.
func claimWidth(fields []Field) int {
	n := 0
	for _, f := range fields {
		if f.Type == TypeObject || f.Type == "object" {
			if members := len(caseClaims(f.Fields, false, nil)); members > 1 {
				n += members
				continue
			}
		}
		n++
	}
	return n
}
