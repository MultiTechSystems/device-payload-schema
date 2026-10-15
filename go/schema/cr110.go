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
	"strconv"
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
	objects := map[string]map[string]bool{} // the paths a merged object combines as objects
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
			// CR-2026-173: a member that is an object is combined member by member too.
			// PS-526 concerns two cases declaring a leaf; one case naming a member twice
			// counts once.
			inCase := map[string]bool{}
			for _, m := range mergedMembers(found[name]["fields"], nil) {
				pk := pathKey(m.path)
				if m.isObject {
					if objects[name] == nil {
						objects[name] = map[string]bool{}
					}
					objects[name][pk] = true
					continue
				}
				if inCase[pk] {
					continue
				}
				inCase[pk] = true
				key := name + "\x01" + pk
				if _, seen := shared[key]; !seen {
					sharedOrder = append(sharedOrder, key)
				}
				shared[key] = append(shared[key], m.decl)
				if m.decl["on_repeat"] != "last" {
					continue
				}
				if info.lastMembers[name] == nil {
					info.lastMembers[name] = map[string]bool{}
				}
				info.lastMembers[name][pk] = true
			}
		}
	}
	for _, key := range sharedOrder {
		decls := shared[key]
		parts := strings.SplitN(key, "\x01", 2)
		dotted := strings.ReplaceAll(parts[1], "\x00", ".")
		if objects[parts[0]][parts[1]] {
			fail("tlv: the merged object '%s' declares '%s' as an object in one case and as a value in another (PS-526)",
				parts[0], dotted)
			continue
		}
		// CR-2026-128: merged cases share a leaf only where it declares `last`, at any
		// depth (CR-2026-173, PS-526).
		if len(decls) < 2 {
			continue
		}
		for _, d := range decls {
			if d["on_repeat"] != "last" {
				fail("tlv: cases share the member '%s' of the merged object '%s' without `on_repeat: last` (PS-526)",
					dotted, parts[0])
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
							// A merged object's leaf may declare `last` at any depth
							// (PS-525, CR-2026-173).
							for _, m := range mergedMembers(found[name]["fields"], nil) {
								if m.decl["on_repeat"] == "last" {
									allowedSet[identity(m.decl)] = true
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
// objects member by member, recursing where both carry an object (CR-2026-173), and a
// leaf reported twice is an error unless it declares `on_repeat: last`. `last` holds
// leaf paths (pathKey).
func combineRepeated(name string, earlier, value any, mode string, last map[string]bool) (any, error) {
	if mode == "last" {
		return value, nil
	}
	return mergeObjects(name, earlier, value, last, nil)
}

func mergeObjects(name string, earlier, value any, last map[string]bool, path []string) (any, error) {
	before, okA := earlier.(map[string]any)
	after, okB := value.(map[string]any)
	if !okA || !okB {
		return value, nil
	}
	combined := make(map[string]any, len(before)+len(after))
	for k, v := range before {
		combined[k] = v
	}
	keys := make([]string, 0, len(after))
	for k := range after {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		v := after[k]
		here := append(append([]string{}, path...), k)
		prior, twice := combined[k]
		if twice && !last[pathKey(here)] {
			_, priorMap := prior.(map[string]any)
			_, valueMap := v.(map[string]any)
			if priorMap && valueMap {
				merged, err := mergeObjects(name, prior, v, last, here)
				if err != nil {
					return nil, err
				}
				combined[k] = merged
				continue
			}
			return nil, fmt.Errorf("tlv: the merged object '%s' reports its member '%s' twice (PS-525)",
				name, strings.Join(here, "."))
		}
		combined[k] = v
	}
	return combined, nil
}

// mergedMember is one named member of a merged object, at any depth (CR-2026-173).
type mergedMember struct {
	path     []string
	decl     map[string]any
	isObject bool
}

// mergedMembers is merged_members: each named member of a merged object with its path,
// where a member of `type: object` not declaring `on_repeat: last` is combined member by
// member and anything else is a leaf.
func mergedMembers(fields any, path []string) []mergedMember {
	var out []mergedMember
	list, _ := fields.([]any)
	for _, item := range list {
		m := asStringMap(item)
		if m == nil {
			continue
		}
		name, _ := m["name"].(string)
		if name == "" {
			continue
		}
		here := append(append([]string{}, path...), name)
		isObject := m["type"] == "object" && m["on_repeat"] != "last"
		out = append(out, mergedMember{here, m, isObject})
		if isObject {
			out = append(out, mergedMembers(m["fields"], here)...)
		}
	}
	return out
}

// pathKey joins a member path into one map key; a name may hold a dot.
func pathKey(path []string) string { return strings.Join(path, "\x00") }

// leafPaths is leaf_paths: a merged object's value's leaf paths (pathKey), descending only
// into the members the schema declares as objects to be combined.
func leafPaths(value map[string]any, objects map[string]bool, path []string, out map[string]bool) {
	for k, v := range value {
		here := append(append([]string{}, path...), k)
		if sub, ok := v.(map[string]any); ok && objects[pathKey(here)] {
			leafPaths(sub, objects, here, out)
			continue
		}
		out[pathKey(here)] = true
	}
}

// valueAt is value_at: the part of a nested value holding exactly the given leaf paths.
func valueAt(value map[string]any, paths map[string]bool) map[string]any {
	out := map[string]any{}
	for pk := range paths {
		path := strings.Split(pk, "\x00")
		src, dst := value, out
		for _, k := range path[:len(path)-1] {
			src, _ = src[k].(map[string]any)
			next, ok := dst[k].(map[string]any)
			if !ok {
				next = map[string]any{}
				dst[k] = next
			}
			dst = next
		}
		dst[path[len(path)-1]] = src[path[len(path)-1]]
	}
	return out
}

// dottedPaths is a set of path keys as `a.b`, quoted and sorted, for a message.
func dottedPaths(paths map[string]bool) []string {
	out := make([]string, 0, len(paths))
	for pk := range paths {
		out = append(out, "'"+strings.ReplaceAll(pk, "\x00", ".")+"'")
	}
	sort.Strings(out)
	return out
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

// tlvEncodePlan is tlv_encode_plan: for each merged object, every leaf path some case
// declares, at any depth (CR-2026-173) - a supplied leaf outside it is an error (PS-527) -
// and the paths it combines as objects.
type encodePlan struct {
	members map[string]map[string]bool
	objects map[string]map[string]bool
}

// givenLeaves is a merged object's input as leaf paths, or nil where it is no mapping.
func (p encodePlan) givenLeaves(name string, data map[string]any) map[string]bool {
	obj, ok := data[name].(map[string]any)
	if !ok {
		return nil
	}
	out := map[string]bool{}
	leafPaths(obj, p.objects[name], nil, out)
	return out
}

func tlvEncodePlan(tlv map[string]any, order keyOrder, info tlvRepeatInfo) encodePlan {
	plan := encodePlan{members: map[string]map[string]bool{}, objects: map[string]map[string]bool{}}
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
			if plan.members[name] == nil {
				plan.members[name] = map[string]bool{}
				plan.objects[name] = map[string]bool{}
			}
			for _, m := range mergedMembers(found[name]["fields"], nil) {
				if m.isObject {
					plan.objects[name][pathKey(m.path)] = true
				} else {
					plan.members[name][pathKey(m.path)] = true
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
		given := p.givenLeaves(name, data)
		strayPaths := map[string]bool{}
		for pk := range given {
			if !p.members[name][pk] {
				strayPaths[pk] = true
			}
		}
		if stray := dottedPaths(strayPaths); len(stray) > 0 {
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
		if f.MatchInline != nil {
			out = append(out, matchClaims(*f.MatchInline)...)
			continue
		}
		if f.Name == "" || strings.HasPrefix(f.Name, "_") {
			continue
		}
		if checksumOf(f) != nil {
			// PS-587 (CR-2026-174): computed, never taken from the input, so never required.
			out = append(out, caseClaim{f, true})
			continue
		}
		if isLiteralField(f) || isComputedField(f) || isEcho(f) {
			// A literal or computed field writes no bytes, but only this case reports it,
			// so it claims its name, never required: a literal where the input gives its
			// value (a literal-only entry), a computed field so that the case is chosen
			// and what it is computed from is asked for (PS-434). Neither claimed anything,
			// so an entry reporting only these was never written (no-silent-empty).
			out = append(out, caseClaim{f, true})
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

// caseTakes is the reference's _case_takes (CR-2026-148, PS-527 amended): whether a case's
// field can take a value, and whether it reproduces it exactly. Fits: the value with the
// field's arithmetic undone (and, for an integer type or bit range, rounded per PS-552)
// lies within the type's range (PS-168); a float takes any finite value no larger than
// its largest. Exact: the arithmetic re-applied to the raw value written, through the
// type's storage, gives the value under binary64 equality. No tolerance anywhere; the
// test this replaces asked for an integer within an absolute 1e-9, which refused every
// float field and 0xfffffe through `div: 100`.
func caseTakes(f Field, value any) (bool, bool) {
	if isByteStringType(f.Type) { // CR-2026-181 (PS-527, PS-601, PS-602)
		ok := byteStringTakes(f, value)
		return ok, ok
	}
	if list, isList := value.([]any); isList && (f.Type == TypeRepeat || f.Type == TypeRepeatLower) {
		return repeatTakes(f, list)
	}
	reversed, err := reverseLookupAnyCode(f, value)
	if err != nil {
		return false, false
	}
	if _, isBool := reversed.(bool); isBool {
		return true, true
	}
	given, ok := toFloat64(reversed)
	if !ok {
		return true, true
	}
	raw, err := reverseArithmetic(given, f)
	if err != nil || math.IsNaN(raw) || math.IsInf(raw, 0) {
		return false, false
	}
	var stored float64
	t := strings.ToLower(string(f.Type))
	if m := bitRangePattern.FindStringSubmatch(t); m != nil {
		start, _ := strconv.Atoi(m[2])
		end, _ := strconv.Atoi(m[3])
		stored = math.RoundToEven(raw)
		if !fitsBits(stored, end-start+1, strings.HasPrefix(t, "s")) {
			return false, false
		}
	} else if bits, signed, isInt := integerTypeBits(t); isInt {
		stored = math.RoundToEven(raw)
		if !fitsBits(stored, bits, signed) {
			return false, false
		}
	} else if max, isFloat := floatTypeMax[t]; isFloat {
		if math.Abs(raw) > max {
			return false, false
		}
		switch t {
		case "f64", "float64":
			stored = raw
		case "f16", "float16":
			stored = f16Round(raw)
		default:
			stored = float64(float32(raw))
		}
	} else if t == "udec" || t == "sdec" {
		n := math.RoundToEven(raw * 10) // PS-556
		low, high := 0.0, 159.0
		if t == "sdec" {
			low, high = -80, 79
		}
		if n < low || n > high {
			return false, false
		}
		whole := math.Floor(n / 10)
		stored = whole + (n-whole*10)*0.1
	} else if t == "bf16" {
		word, err := encodeBF16(raw, f.Name) // through the type's storage is PS-559's rounding
		if err != nil {
			return false, false
		}
		stored = decodeBF16(word)
	} else if t == "uflt16" || t == "sflt16" || t == "sflt24" {
		word, err := encodeMinifloat(FieldType(t), raw)
		if err != nil {
			return false, false
		}
		v, ok := decodeMinifloat(FieldType(t), word)
		if !ok {
			return false, false
		}
		stored = v
	} else {
		return true, true
	}
	forward := applyTransformStages(applyCanonicalModifiers(stored, f), f.Transform)
	return true, forward == given
}

// floatTypeMax is the largest finite magnitude of each binary float type (CR-2026-148).
var floatTypeMax = map[string]float64{
	"f16": 65504, "float16": 65504,
	"f32": math.MaxFloat32, "float32": math.MaxFloat32,
	"f32le16": math.MaxFloat32, "f32be16le": math.MaxFloat32,
	"f64": math.Inf(1), "float64": math.Inf(1),
}

// integerTypeBits is an integer type's width and sign, word-ordered and 64-bit included.
func integerTypeBits(t string) (int, bool, bool) {
	switch t {
	case "u8", "uint8", "byte":
		return 8, false, true
	case "u16", "uint16":
		return 16, false, true
	case "u24", "uint24":
		return 24, false, true
	case "u32", "uint32", "u32le16", "u32be16le":
		return 32, false, true
	case "u64", "uint64":
		return 64, false, true
	case "s8", "i8", "int8":
		return 8, true, true
	case "s16", "i16", "int16":
		return 16, true, true
	case "s24", "i24", "int24":
		return 24, true, true
	case "s32", "i32", "int32", "s32le16", "s32be16le":
		return 32, true, true
	case "s64", "i64", "int64":
		return 64, true, true
	}
	return 0, false, false
}

// fitsBits is whether an integer value lies within `bits` bits, signed or not.
func fitsBits(v float64, bits int, signed bool) bool {
	if signed {
		return v >= -math.Ldexp(1, bits-1) && v < math.Ldexp(1, bits-1)
	}
	return v >= 0 && v < math.Ldexp(1, bits)
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

// matchClaims is the reference's _match_claims: a nameless inline match claims its `name`
// and every branch's fields, each optional, since which branch supplies a name depends on
// the data; each name once. Not descending into it, a tlv case whose only content is a
// match claimed nothing and was never written - the encode returned an empty payload
// with no error - and an object holding one refused every value.
func matchClaims(m Field) []caseClaim {
	var out []caseClaim
	seen := map[string]bool{}
	if m.Name != "" {
		claim := Field{Name: m.Name}
		switch m.Length {
		case 0, 1:
			claim.Type = TypeU8
		case 2:
			claim.Type = TypeU16
		case 3:
			claim.Type = "u24"
		case 4:
			claim.Type = TypeU32
		case 8:
			claim.Type = "u64"
		}
		out = append(out, caseClaim{claim, true})
		seen[m.Name] = true
	}
	var bodies [][]Field
	for _, c := range m.Cases {
		bodies = append(bodies, c.Fields)
	}
	switch fallback := m.MatchDefault.(type) {
	case []any:
		bodies = append(bodies, parseFieldsRaw(fallback))
	case []Field:
		bodies = append(bodies, fallback)
	}
	for _, body := range bodies {
		for _, c := range caseClaims(body, true, nil) {
			// Branches report one name as different literals: each is a claim, since the
			// input's value says which branch it came from.
			key := c.field.Name
			if isLiteralField(c.field) {
				key = c.field.Name + "\x00" + pyRepr(c.field.Value)
			}
			if !seen[key] {
				seen[key] = true
				out = append(out, caseClaim{c.field, true})
			}
		}
	}
	return out
}

// f16Round is a value rounded to the nearest IEEE half, a tie to even, straight from
// binary64 (the reference's struct 'e'), for the exactness test.
func f16Round(x float64) float64 {
	if x == 0 || math.IsInf(x, 0) || math.IsNaN(x) {
		return x
	}
	a := math.Abs(x)
	_, exp := math.Frexp(a) // a = m * 2^exp, 0.5 <= m < 1
	e := exp - 1
	if e < -14 {
		e = -14
	}
	q := math.Ldexp(1, e-10)
	r := math.RoundToEven(a/q) * q
	if x < 0 {
		return -r
	}
	return r
}

// mergedLeafKeys is a merged object field's leaf paths (pathKey), at every depth, read
// from its typed members (CR-2026-173).
func mergedLeafKeys(f Field) map[string]bool {
	out := map[string]bool{}
	var walk func(fields []Field, path []string)
	walk = func(fields []Field, path []string) {
		for _, m := range fields {
			if m.Name == "" {
				continue
			}
			here := append(append([]string{}, path...), m.Name)
			if (m.Type == TypeObject || m.Type == "object") && m.decl["on_repeat"] != "last" {
				walk(m.Fields, here)
				continue
			}
			out[pathKey(here)] = true
		}
	}
	walk(f.Fields, nil)
	return out
}

// mergedClaimWidth is claimWidth with a merged object counting its leaves, at every
// depth (PS-527 as CR-2026-173 amends it).
func mergedClaimWidth(fields []Field, repeat tlvRepeatInfo) int {
	n := 0
	for _, f := range fields {
		if (f.Type == TypeObject || f.Type == "object") && repeat.mode(f.Name) == "merge" {
			if leaves := len(mergedLeafKeys(f)); leaves > 0 {
				n += leaves
				continue
			}
		}
		n += claimWidth([]Field{f})
	}
	return n
}
