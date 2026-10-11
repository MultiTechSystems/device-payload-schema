package schema

// 0.5.2 wave 6a: the repeat iterator and reserved trailers.
//
// CR-2026-048 (reserve, trailer), CR-2026-081 (tlv reserve), CR-2026-053 (index,
// count_as, present_if), CR-2026-080 (count_as after present_if), CR-2026-055 (carry)
// and CR-2026-054's identity and per-element annotations. Each function mirrors the one
// of the same purpose in tools/schema_interpreter.py: iterator_errors,
// _repeat_member_ref_errors, tlv_reserve_errors, repeat_only_names, _ref,
// _carry_initial, and the element/trailer handling of _decode_repeat and
// _decode_field_list.

import (
	"fmt"
	"regexp"
	"strings"
)

// --- parsing -----------------------------------------------------------------

// intValue reads a whole number from a YAML int or a JSON float64; never a bool.
func intValue(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case float64:
		if n == float64(int(n)) {
			return int(n), true
		}
	}
	return 0, false
}

// parseGuardCondition reads one `{field: $x, <op>: value}` condition.
func parseGuardCondition(raw map[string]any) *GuardCondition {
	gc := &GuardCondition{}
	gc.Field, _ = raw["field"].(string)
	bound := func(key string) *float64 {
		if _, isBool := raw[key].(bool); isBool {
			return nil
		}
		if v, ok := toFloat64(raw[key]); ok && raw[key] != nil {
			return &v
		}
		return nil
	}
	gc.Gt, gc.Gte, gc.Lt = bound("gt"), bound("gte"), bound("lt")
	gc.Lte, gc.Eq, gc.Ne = bound("lte"), bound("eq"), bound("ne")
	parseSetConditions(gc, raw) // PS-543
	return gc
}

// parseIteratorKeys reads the wave 6a keys of a repeat, a tlv block or an element field.
func parseIteratorKeys(f *Field, fm map[string]any) {
	if reserve, ok := intValue(fm["reserve"]); ok {
		f.Reserve = reserve
	}
	if trailer, ok := fm["trailer"].([]any); ok {
		f.Trailer = parseFieldsRaw(trailer)
	}
	f.Index, _ = fm["index"].(string)
	f.CountAs, _ = fm["count_as"].(string)
	if cond := asStringMap(fm["present_if"]); cond != nil {
		f.PresentIf = parseGuardCondition(cond)
	}
	if carry, ok := fm["carry"]; ok {
		f.Carry = carry
	}
	// PS-530: the index's base; a non-integer is refused at load (elementRepeatError).
	if base, ok := intValue(fm["index_base"]); ok {
		f.IndexBase = base
	}
	// PS-528: a repeat of single values decodes its element as a one-field record.
	if members, name := rawRepeatMembers(fm); name != "" {
		f.Fields = parseFieldsRaw(members)
		f.ElementName = name
	}
}

// elementName is ELEMENT_NAME: the name an `element` is decoded under where it declares
// none (PS-528). It is never reported.
const elementName = "element"

// rawRepeatMembers is repeat_members: a repeat's element fields as written and, for a
// repeat of single values, the element's name - its own, or "element". The element is
// returned as a copy carrying that name, so present_if and name_from can refer to it.
func rawRepeatMembers(f map[string]any) ([]any, string) {
	_, hasFields := f["fields"]
	if element := asStringMap(f["element"]); element != nil && !hasFields {
		name, _ := element["name"].(string)
		if name == "" {
			name = elementName
		}
		named := make(map[string]any, len(element)+1)
		for k, v := range element {
			named[k] = v
		}
		named["name"] = name
		return []any{named}, name
	}
	members, _ := f["fields"].([]any)
	return members, ""
}

// elementRepeatError is element_repeat_errors: PS-083 and PS-528 (exactly one of
// `fields` and `element`, and an element is one field definition) and PS-530 (an
// integer index_base). Worded as the spec prototype words it.
func elementRepeatError(f map[string]any) error {
	name, ok := f["name"].(string)
	if !ok || name == "" {
		name = "<unnamed>"
	}
	label := fmt.Sprintf("Repeat '%s'", name)
	_, hasFields := f["fields"]
	element, hasElement := f["element"]
	if hasFields == hasElement {
		which := "neither"
		if hasFields {
			which = "both"
		}
		return fmt.Errorf("%s declares %s of `fields` and `element`; it declares exactly one (PS-083, PS-528)", label, which)
	}
	if hasElement && asStringMap(element) == nil {
		return fmt.Errorf("%s: `element` is a single field definition (PS-528)", label)
	}
	if base, present := f["index_base"]; present {
		if _, ok := intValue(base); !ok {
			return fmt.Errorf("%s: `index_base` is an integer (PS-530)", label)
		}
	}
	return nil
}

// elementValue is element_value: the value a repeat of single values reports for one
// element, or nil where it was omitted (PS-528), so positions keep their index.
// Interpreter metadata beside it is not the value.
func elementValue(record map[string]any) any {
	for key, value := range record {
		if key != "_quality" && key != "_warnings" && key != "_meta" {
			return value
		}
	}
	return nil
}

// --- schema rules, checked at load --------------------------------------------

var computedTypeNames = map[string]bool{"number": true, "integer": true}

var guardOps = []string{"gt", "gte", "lt", "lte", "eq", "ne", "in", "not_in"}

var templateReference = regexp.MustCompile(`\$\{([^}]+)\}`)

// isRawLiteral is is_literal: a `string` or `number` field declaring `value` (PS-357).
func isRawLiteral(f map[string]any) bool {
	_, hasValue := f["value"]
	return hasValue && (f["type"] == "string" || f["type"] == "number" || f["type"] == "boolean")
}

// repeatMemberRefError holds PS-373 and PS-374: a per-element unit, IPSO instance or
// SenML name, and what it names.
func repeatMemberRefError(repeat, member map[string]any, where string) error {
	byName := map[string]map[string]any{}
	if list, _ := rawRepeatMembers(repeat); list != nil {
		for _, raw := range list {
			if f := asStringMap(raw); f != nil {
				if name, ok := f["name"].(string); ok {
					byName[name] = f
				}
			}
		}
	}
	index, _ := repeat["index"].(string)
	type ref struct{ key, name string }
	var refs []ref
	if unit, ok := member["unit"].(string); ok && strings.HasPrefix(unit, "$") {
		refs = append(refs, ref{"unit", unit[1:]})
	}
	if ipso := asStringMap(member["ipso"]); ipso != nil {
		if instance, ok := ipso["instance"].(string); ok && strings.HasPrefix(instance, "$") {
			refs = append(refs, ref{"ipso.instance", instance[1:]})
		}
	}
	if senml := asStringMap(member["senml"]); senml != nil {
		if name, ok := senml["name"].(string); ok {
			for _, m := range templateReference.FindAllStringSubmatch(name, -1) {
				refs = append(refs, ref{"senml.name", m[1]})
			}
		}
	}
	for _, r := range refs {
		if index != "" && r.name == index {
			_, literalCount := intValue(repeat["count"])
			_, hasMax := repeat["max"]
			if !literalCount && !hasMax {
				return fmt.Errorf("%s: %s uses the index '%s', so the repeat needs a literal count or a max (PS-374)",
					where, r.key, r.name)
			}
			continue
		}
		target, found := byName[r.name]
		if !found {
			return fmt.Errorf("%s: %s names '%s', which is neither a field of the element nor the repeat's index (PS-373)",
				where, r.key, r.name)
		}
		if _, hasLookup := target["lookup"]; !hasLookup && !isRawLiteral(target) {
			return fmt.Errorf("%s: %s names '%s', which must carry a lookup or be a literal so its values are known from the schema (PS-374)",
				where, r.key, r.name)
		}
	}
	return nil
}

// iteratorError is iterator_errors: PS-350, PS-383, PS-384 (reserve and trailer),
// PS-369 (index and count_as names), PS-372 to PS-374 (identity and per-element
// references), PS-381 (carry) and present_if's form (PS-386). It walks nested field
// lists so an enclosing repeat's names are known.
func iteratorError(fields any, enclosing []string, inElements bool, where string) error {
	list, _ := fields.([]any)
	for i, raw := range list {
		f := asStringMap(raw)
		if f == nil {
			continue
		}
		name, _ := f["name"].(string)
		if name == "" {
			name = "?"
		}
		at := fmt.Sprintf("%s[%d] (%s)", where, i, name)
		typ, _ := f["type"].(string)
		if carry, ok := f["carry"]; ok {
			if !inElements || !computedTypeNames[typ] {
				return fmt.Errorf("%s: carry applies only to a computed field declared in a repeat's elements (PS-381)", at)
			}
			_, isBool := carry.(bool)
			_, isNumber := toFloat64(carry)
			text, isString := carry.(string)
			if isBool || !(isNumber && !isString || isString && strings.HasPrefix(text, "$")) {
				return fmt.Errorf("%s: carry must be a numeric literal or a $ reference to a field decoded before the repeat (PS-381)", at)
			}
		}
		if typ == "repeat" {
			if err := elementRepeatError(f); err != nil { // PS-528, PS-530
				return err
			}
			members, element := rawRepeatMembers(f)
			membersKey := "fields"
			if element != "" {
				membersKey = "element"
			}
			names := map[string]bool{}
			for _, m := range members {
				if mm := asStringMap(m); mm != nil {
					if n, ok := mm["name"].(string); ok {
						names[n] = true
					}
				}
			}
			var own []string
			for _, key := range []string{"index", "count_as"} {
				value, present := f[key]
				if !present || value == nil {
					continue
				}
				text, ok := value.(string)
				if !ok || text == "" {
					return fmt.Errorf("%s: %s must be a name (PS-366, PS-367)", at, key)
				}
				if names[text] || containsString(enclosing, text) || containsString(own, text) {
					return fmt.Errorf("%s: %s '%s' names a field of the element, or an index or count_as already in scope (PS-369)", at, key, text)
				}
				own = append(own, text)
			}
			reserveRaw, hasReserve := f["reserve"]
			if hasReserve {
				if f["until"] != "end" {
					return fmt.Errorf("%s: reserve applies only to a repeat with until: end (PS-350)", at)
				}
				if n, ok := intValue(reserveRaw); !ok || n < 0 {
					return fmt.Errorf("%s: reserve must be a non-negative integer (PS-350)", at)
				}
			}
			if trailerRaw, hasTrailer := f["trailer"]; hasTrailer {
				trailer, isList := trailerRaw.([]any)
				size, known := 0, false
				if isList {
					size, known = fixedElementSizeKnown(parseFieldsRaw(trailer))
				}
				reserve, _ := intValue(reserveRaw)
				switch {
				case !hasReserve:
					return fmt.Errorf("%s: trailer needs reserve (PS-383)", at)
				case !known:
					return fmt.Errorf("%s: every trailer field needs a size known from the schema (PS-384)", at)
				case size != reserve:
					return fmt.Errorf("%s: the trailer's fields take %d byte(s), and reserve is %d; they must be equal (PS-384)", at, size, reserve)
				}
			}
			if presentIf, ok := f["present_if"]; ok && presentIf != nil {
				cond := asStringMap(presentIf)
				_, fieldIsString := cond["field"].(string)
				if cond == nil || !fieldIsString || !hasAnyKey(cond, guardOps) {
					return fmt.Errorf("%s: present_if must be one guard condition, {field: $name, <op>: value} (PS-386)", at)
				}
			}
			if identity, ok := f["identity"]; ok && identity != nil {
				text, isString := identity.(string)
				index, _ := f["index"].(string)
				valid := isString && ((index != "" && text == index) ||
					(strings.HasPrefix(text, "$") && names[text[1:]]))
				if !valid {
					return fmt.Errorf("%s: identity must be the repeat's index or a $ reference to a field of its elements (PS-372)", at)
				}
			}
			for j, m := range members {
				if mm := asStringMap(m); mm != nil {
					mname, _ := mm["name"].(string)
					if mname == "" {
						mname = "?"
					}
					if err := repeatMemberRefError(f, mm, fmt.Sprintf("%s.%s[%d] (%s)", at, membersKey, j, mname)); err != nil {
						return err
					}
				}
			}
			inner := append(append([]string{}, enclosing...), own...)
			if err := iteratorError(members, inner, true, at+"."+membersKey); err != nil {
				return err
			}
			if trailer, ok := f["trailer"].([]any); ok {
				if err := iteratorError(trailer, enclosing, false, at+".trailer"); err != nil {
					return err
				}
			}
			continue
		}
		if nested, ok := f["fields"].([]any); ok {
			if err := iteratorError(nested, enclosing, false, at+".fields"); err != nil {
				return err
			}
		}
		for _, construct := range []string{"match", "tlv", "flagged", "byte_group"} {
			body := asStringMap(f[construct])
			if body == nil {
				continue
			}
			var groups []any
			switch cases := body["cases"].(type) {
			case []any:
				groups = cases
			default:
				for _, c := range asStringMap(cases) {
					groups = append(groups, c)
				}
			}
			for _, c := range groups {
				group := c
				if m := asStringMap(c); m != nil {
					group = m["fields"]
				}
				if err := iteratorError(group, enclosing, false, at+"."+construct); err != nil {
					return err
				}
			}
			if err := iteratorError(body["fields"], enclosing, false, at+"."+construct); err != nil {
				return err
			}
			if gs, ok := body["groups"].([]any); ok {
				for _, g := range gs {
					if gm := asStringMap(g); gm != nil {
						if err := iteratorError(gm["fields"], enclosing, false, at+"."+construct); err != nil {
							return err
						}
					}
				}
			}
		}
	}
	return nil
}

func containsString(list []string, s string) bool {
	for _, item := range list {
		if item == s {
			return true
		}
	}
	return false
}

// tlvReserveError is tlv_reserve_errors: a tlv's reserve is a non-negative integer
// (PS-471).
func tlvReserveError(node map[string]any) error {
	body := asStringMap(node["tlv"])
	if body == nil {
		return nil
	}
	reserve, present := body["reserve"]
	if !present {
		return nil
	}
	if n, ok := intValue(reserve); !ok || n < 0 {
		return fmt.Errorf("tlv reserve must be a non-negative integer, got %v (PS-471)", reserve)
	}
	return nil
}

// checkIteratorRules is schema_iterator_errors: iteratorError over the top-level and
// port field lists, and every tlv's reserve.
func checkIteratorRules(raw map[string]any) error {
	if err := iteratorError(raw["fields"], nil, false, "fields"); err != nil {
		return err
	}
	for port, entry := range asStringMap(raw["ports"]) {
		group := entry
		if m := asStringMap(entry); m != nil {
			group = m["fields"]
		}
		if err := iteratorError(group, nil, false, fmt.Sprintf("ports[%s].fields", port)); err != nil {
			return err
		}
	}
	var walk func(node any) error
	walk = func(node any) error {
		if m := asStringMap(node); m != nil {
			if _, hasTLV := m["tlv"]; hasTLV {
				if typ, _ := m["type"].(string); typ == "" {
					if err := tlvReserveError(m); err != nil {
						return err
					}
				}
			}
			for key, value := range m {
				if key == "test_vectors" || key == "definitions" {
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

// repeatOnlyNames is repeat_only_names: names declared in some repeat's elements and
// nowhere outside one (PS-368).
func repeatOnlyNames(raw map[string]any) map[string]bool {
	inside, outside := map[string]bool{}, map[string]bool{}
	var walk func(node any, inElements bool)
	walk = func(node any, inElements bool) {
		if m := asStringMap(node); m != nil {
			_, hasType := m["type"]
			_, hasFields := m["fields"]
			if name, ok := m["name"].(string); ok && (hasType || hasFields) {
				if inElements {
					inside[name] = true
				} else {
					outside[name] = true
				}
			}
			for key, value := range m {
				if key == "test_vectors" || key == "definitions" {
					continue
				}
				walk(value, inElements || ((key == "fields" || key == "element") && m["type"] == "repeat"))
			}
		} else if list, ok := node.([]any); ok {
			for _, item := range list {
				walk(item, inElements)
			}
		}
	}
	walk(raw, false)
	only := map[string]bool{}
	for name := range inside {
		if !outside[name] {
			only[name] = true
		}
	}
	return only
}

// --- decoding ------------------------------------------------------------------

// scopeError is the PS-368 half of _ref, consulted where a `$name` reference is
// unbound: a field of this element not yet decoded, or a name that only ever exists
// inside some repeat's elements, is an error. Any other unbound reference keeps the
// behaviour its call site already had.
func (ctx *DecodeContext) scopeError(name string) error {
	if ctx.elementNames[name] {
		return fmt.Errorf("$%s refers to a field of this element that is not yet decoded (PS-368)", name)
	}
	if ctx.repeatOnly[name] {
		return fmt.Errorf("$%s is declared only inside a repeat's elements, so it has no value here (PS-368)", name)
	}
	return nil
}

// guardScopeError checks each condition's reference for PS-368 before a guard is
// evaluated, since guard evaluation itself reads an unbound name as a failed condition.
func guardScopeError(conds []GuardCondition, ctx *DecodeContext) error {
	for _, cond := range conds {
		name := strings.TrimPrefix(cond.Field, "$")
		if _, bound := ctx.Variables[name]; !bound {
			if err := ctx.scopeError(name); err != nil {
				return err
			}
		}
	}
	return nil
}

// carryInitial is _carry_initial: a carried field's value before the first element
// (PS-378).
func carryInitial(field Field, ctx *DecodeContext) (any, error) {
	if ref, ok := field.Carry.(string); ok && strings.HasPrefix(ref, "$") {
		value, bound := ctx.Variables[ref[1:]]
		if !bound {
			return nil, fmt.Errorf("carry of '%s' names %s, which was not decoded before the repeat (PS-381)", field.Name, ref)
		}
		return value, nil
	}
	return field.Carry, nil
}

// repeatElements decodes a repeat's elements, each in its own scope (PS-368).
//
// The returned element function decodes one element at the current offset and appends
// it unless present_if drops it (PS-386). The index is bound while it is decoded
// (PS-366), and carried fields see their previous value (PS-379). Names bound inside
// the element are gone after it.
type repeatElements struct {
	field      Field
	ctx        *DecodeContext
	names      map[string]bool
	carry      map[string]any
	carryOrder []string
	iterations int
	result     []any
}

func newRepeatElements(field Field, ctx *DecodeContext) (*repeatElements, error) {
	r := &repeatElements{field: field, ctx: ctx, names: map[string]bool{}, carry: map[string]any{},
		result: []any{}}
	for _, nested := range field.Fields {
		if nested.Name != "" {
			r.names[nested.Name] = true
		}
		// PS-378, PS-380: a carried field starts again from its `carry` value each time
		// the repeat begins, including each iteration of an enclosing repeat.
		if nested.Carry != nil {
			initial, err := carryInitial(nested, ctx)
			if err != nil {
				return nil, err
			}
			r.carry[nested.Name] = initial
			r.carryOrder = append(r.carryOrder, nested.Name)
		}
	}
	return r, nil
}

func (r *repeatElements) element() error {
	ctx := r.ctx
	outer := make(map[string]any, len(ctx.Variables))
	for k, v := range ctx.Variables {
		outer[k] = v
	}
	savedNames := ctx.elementNames
	ctx.elementNames = r.names
	if r.field.Index != "" {
		// PS-366, PS-530: the position counted from index_base.
		ctx.Variables[r.field.Index] = r.iterations + r.field.IndexBase
	}
	for _, name := range r.carryOrder {
		ctx.Variables[name] = r.carry[name]
	}

	// PS-541: the records this element's `records` entry yields, if it reaches one.
	savedRecords := ctx.pendingRecords
	records := []map[string]any{}
	ctx.pendingRecords = &records
	element, err := decodeFields(r.field.Fields, ctx)
	ctx.pendingRecords = savedRecords
	keep := true
	if err == nil {
		for _, name := range r.carryOrder {
			if value, bound := ctx.Variables[name]; bound {
				r.carry[name] = value
			}
		}
		if r.field.PresentIf != nil {
			// The element is whole here, so a name of it with no value is absent, not
			// "not yet decoded", and the condition fails (PS-540). No PS-368 check.
			keep = guardConditionsHold(&GuardDef{When: []GuardCondition{*r.field.PresentIf}}, ctx)
		}
	}

	// PS-368: an element's names do not outlive it.
	for k := range ctx.Variables {
		delete(ctx.Variables, k)
	}
	for k, v := range outer {
		ctx.Variables[k] = v
	}
	ctx.elementNames = savedNames
	if err != nil {
		return err
	}
	r.iterations++
	if keep && len(records) > 0 {
		// PS-541: the records, as consecutive elements, in place of the element; the
		// element's other reported fields go into each, before its own.
		for _, record := range records {
			merged := make(map[string]any, len(element)+len(record))
			for k, v := range element {
				merged[k] = v
			}
			for k, v := range record {
				merged[k] = v
			}
			r.result = append(r.result, merged)
		}
	} else if keep {
		if r.field.ElementName != "" {
			// PS-528: the element's value, or nil where it was omitted.
			r.result = append(r.result, elementValue(element))
		} else {
			r.result = append(r.result, element)
		}
	}
	return nil
}

// decodeTrailer decodes a repeat's trailer from the reserved bytes before its first
// element, into the enclosing scope (PS-383): its names are bound for the elements and
// every later field, and it is reported beside the repeat.
func decodeTrailer(field Field, ctx *DecodeContext, schema *Schema) (map[string]any, error) {
	start := len(ctx.Data) - field.Reserve
	if start < ctx.Offset {
		return nil, fmt.Errorf("repeat '%s' reserves %d byte(s) but %d remain at offset %d (PS-351)",
			field.Name, field.Reserve, ctx.Remaining(), ctx.Offset)
	}
	saved := ctx.Offset
	ctx.Offset = start
	decoded, err := decodeFieldsWithSchema(field.Trailer, ctx, schema)
	ctx.Offset = saved
	return decoded, err
}

// --- encoding ------------------------------------------------------------------

// presentIfBlocksEncode is PS-387: an element present_if dropped is not in the data,
// and its bytes were in the payload, so they cannot be written back. Only elements that
// read nothing (computed fields and literals) can be encoded.
func presentIfBlocksEncode(field Field) bool {
	if field.PresentIf == nil {
		return false
	}
	for _, f := range field.Fields {
		computed := f.Type == TypeNumber || f.Type == "number" || f.IntegerResult
		literal := f.Value != nil && (f.Type == TypeString || f.Type == TypeStringLower || f.Type == TypeBoolean || computed)
		if !computed && !literal {
			return true
		}
	}
	return false
}
