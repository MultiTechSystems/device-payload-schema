package schema

// CR-2026-118: several records from one repeat element (PS-541, PS-542), and the set
// conditions `in` and `not_in` (PS-543). Each function mirrors the one of the same
// purpose in tools/schema_interpreter.py: records_reachable, records_errors,
// condition_errors, the `records` branch of _decode_field_list and the element step of
// _decode_repeat.

import (
	"errors"
	"fmt"
	"sort"
	"strings"
)

// parseRecords reads a `records` entry: a list of field lists. A malformed entry is
// checkRecordsRules' to report; what is not a list parses as an empty record.
func parseRecords(bodies []any) [][]Field {
	out := make([][]Field, 0, len(bodies))
	for _, body := range bodies {
		list, _ := body.([]any)
		out = append(out, parseFieldsRaw(list))
	}
	return out
}

// parseSetConditions reads a condition's `in` and `not_in` (PS-543).
func parseSetConditions(gc *GuardCondition, raw map[string]any) {
	read := func(key string) ([]float64, bool) {
		list, ok := raw[key].([]any)
		if !ok {
			return nil, false
		}
		values := make([]float64, 0, len(list))
		for _, item := range list {
			if v, ok := toFloat64(item); ok {
				values = append(values, v)
			}
		}
		return values, true
	}
	gc.In, gc.HasIn = read("in")
	gc.NotIn, gc.HasNotIn = read("not_in")
}

// setConditionHolds is PS-543: `in` holds where the value equals one of its numbers,
// `not_in` where it equals none.
func setConditionHolds(cond GuardCondition, fv float64) bool {
	contains := func(values []float64) bool {
		for _, v := range values {
			if v == fv {
				return true
			}
		}
		return false
	}
	if cond.HasIn && !contains(cond.In) {
		return false
	}
	if cond.HasNotIn && contains(cond.NotIn) {
		return false
	}
	return true
}

// decodeRecords decodes a `records` entry: each field list, in order, from the read
// position, into a record of its own, collected for the enclosing element (PS-541).
func decodeRecords(field Field, ctx *DecodeContext, schema *Schema) error {
	if ctx.pendingRecords == nil {
		return errors.New("`records` belongs in a repeat's element, or a match case within it (PS-541)")
	}
	for _, body := range field.Records {
		record, err := decodeFieldsWithSchema(body, ctx, schema)
		if err != nil {
			return err
		}
		*ctx.pendingRecords = append(*ctx.pendingRecords, record)
	}
	return nil
}

// recordsReachable is records_reachable for parsed fields: whether a field list reaches
// a `records` entry, directly or through a match case or default.
func recordsReachable(fields []Field) bool {
	for _, f := range fields {
		if f.Records != nil {
			return true
		}
		if m := f.MatchInline; m != nil {
			for _, c := range m.Cases {
				if recordsReachable(c.Fields) {
					return true
				}
			}
			if def, ok := m.MatchDefault.([]any); ok && rawRecordsReachable(def) {
				return true
			}
		}
	}
	return false
}

// rawRecordsReachable is records_reachable over a field list as written.
func rawRecordsReachable(fields any) bool {
	list, _ := fields.([]any)
	for _, item := range list {
		f := asStringMap(item)
		if f == nil {
			continue
		}
		if _, ok := f["records"]; ok && f["type"] == nil {
			return true
		}
		if match := asStringMap(f["match"]); match != nil && f["type"] == nil {
			cases := asStringMap(match["cases"])
			for _, key := range sortedKeys(cases) {
				if rawRecordsReachable(cases[key]) {
					return true
				}
			}
			if rawRecordsReachable(match["default"]) {
				return true
			}
		}
	}
	return false
}

// checkRecordsRules is records_errors and condition_errors: a `records` entry is a
// non-empty list of field lists, in a repeat's element or a match case within it, never
// reached from an `element` (PS-541); a condition's `in` or `not_in` is a list of
// numbers and its one operator (PS-126, PS-543). The wording is the reference's.
func checkRecordsRules(raw map[string]any) error {
	var walk func(fields any, inElement bool) error
	walkCases := func(cases any, inElement bool) error {
		m := asStringMap(cases)
		for _, key := range sortedKeys(m) {
			if err := walk(m[key], inElement); err != nil {
				return err
			}
		}
		return nil
	}
	walk = func(fields any, inElement bool) error {
		list, _ := fields.([]any)
		for _, item := range list {
			f := asStringMap(item)
			if f == nil {
				continue
			}
			if bodies, present := f["records"]; present && f["type"] == nil {
				if !inElement {
					return errors.New("`records` belongs in a repeat's element, or a match case within it (PS-541)")
				}
				listed, ok := bodies.([]any)
				if ok && len(listed) > 0 {
					for _, body := range listed {
						if _, isList := body.([]any); !isList {
							ok = false
						}
					}
				}
				if !ok || len(listed) == 0 {
					return errors.New("`records` is a non-empty list of field lists (PS-541)")
				}
				for _, body := range listed {
					if err := walk(body, false); err != nil {
						return err
					}
				}
				continue
			}
			if f["type"] == "repeat" {
				if element := asStringMap(f["element"]); element != nil {
					if rawRecordsReachable([]any{element}) {
						name, ok := f["name"].(string)
						if !ok || name == "" {
							name = "<unnamed>"
						}
						return fmt.Errorf("Repeat '%s': an `element` cannot reach a `records` entry (PS-541)", name)
					}
					if err := walk([]any{element}, false); err != nil {
						return err
					}
				}
				if err := walk(f["fields"], true); err != nil {
					return err
				}
				if err := walk(f["trailer"], false); err != nil {
					return err
				}
				if clash := recordsClash(f["fields"]); len(clash) > 0 {
					name, ok := f["name"].(string)
					if !ok {
						name = "<unnamed>"
					}
					return fmt.Errorf("Repeat '%s': ['%s'] is reported both by a record and by the element outside `records` (PS-541)",
						name, strings.Join(clash, "', '"))
				}
				continue
			}
			if match := asStringMap(f["match"]); match != nil && f["type"] == nil {
				if err := walkCases(match["cases"], inElement); err != nil {
					return err
				}
				if err := walk(match["default"], inElement); err != nil {
					return err
				}
			}
			if err := walkCases(f["cases"], false); err != nil {
				return err
			}
			if err := walk(f["fields"], false); err != nil {
				return err
			}
			if tlv := asStringMap(f["tlv"]); tlv != nil {
				if err := walkCases(tlv["cases"], false); err != nil {
					return err
				}
			}
			if flagged := asStringMap(f["flagged"]); flagged != nil {
				groups, _ := flagged["groups"].([]any)
				for _, g := range groups {
					if err := walk(asStringMap(g)["fields"], false); err != nil {
						return err
					}
				}
			}
			switch group := f["byte_group"].(type) {
			case []any:
				if err := walk(group, false); err != nil {
					return err
				}
			case map[string]any, map[any]any:
				if err := walk(asStringMap(group)["fields"], false); err != nil {
					return err
				}
			}
		}
		return nil
	}
	if err := walk(raw["fields"], false); err != nil {
		return err
	}
	ports := asStringMap(raw["ports"])
	for _, key := range sortedKeys(ports) {
		entry := ports[key]
		if m := asStringMap(entry); m != nil {
			entry = m["fields"]
		}
		if err := walk(entry, false); err != nil {
			return err
		}
	}
	return checkSetConditions(raw)
}

// levelNames is level_names: the names a field list reports at its own level - its
// named fields and those of the constructs merging into it (match and tlv cases and
// default, flagged groups, byte_group members). An object or repeat gives its own name;
// internal and name_from fields, and `records` bodies, give none.
func levelNames(fields any, found map[string]bool) {
	list, _ := fields.([]any)
	for _, item := range list {
		f := asStringMap(item)
		if f == nil {
			continue
		}
		if _, ok := f["records"]; ok && f["type"] == nil {
			continue
		}
		if t, ok := f["type"]; ok && t != nil && t != "" {
			name, _ := f["name"].(string)
			_, templated := f["name_from"]
			if name != "" && !strings.HasPrefix(name, "_") && (!templated || f["name_from"] == nil || f["name_from"] == "") {
				found[name] = true
			}
			continue
		}
		for _, key := range []string{"match", "tlv"} {
			if spec := asStringMap(f[key]); spec != nil {
				cases := asStringMap(spec["cases"])
				for _, k := range sortedKeys(cases) {
					levelNames(cases[k], found)
				}
				levelNames(spec["default"], found)
			}
		}
		if flagged := asStringMap(f["flagged"]); flagged != nil {
			groups, _ := flagged["groups"].([]any)
			for _, g := range groups {
				if gm := asStringMap(g); gm != nil {
					levelNames(gm["fields"], found)
				}
			}
		}
		switch group := f["byte_group"].(type) {
		case []any:
			levelNames(group, found)
		case map[string]any, map[any]any:
			levelNames(asStringMap(group)["fields"], found)
		}
	}
}

// recordsClash is records_clash: the names a record shares with a field the element
// reports outside `records`, sorted (PS-541, CR-2026-124).
func recordsClash(fields any) []string {
	outside := map[string]bool{}
	levelNames(fields, outside)
	inside := map[string]bool{}
	var collect func(items any)
	collect = func(items any) {
		list, _ := items.([]any)
		for _, item := range list {
			f := asStringMap(item)
			if f == nil {
				continue
			}
			if bodies, ok := f["records"]; ok && f["type"] == nil {
				listed, _ := bodies.([]any)
				for _, body := range listed {
					levelNames(body, inside)
				}
			}
			if match := asStringMap(f["match"]); match != nil && f["type"] == nil {
				cases := asStringMap(match["cases"])
				for _, k := range sortedKeys(cases) {
					collect(cases[k])
				}
				collect(match["default"])
			}
		}
	}
	collect(fields)
	var clash []string
	for name := range inside {
		if outside[name] {
			clash = append(clash, name)
		}
	}
	sort.Strings(clash)
	return clash
}

// checkSetConditions is condition_errors: PS-126, exactly one comparison operator in
// every guard condition and present_if; PS-543, `in` and `not_in` take a list of
// numbers. The wording is the spec prototype's.
func checkSetConditions(node any) error {
	check := func(cond map[string]any, where string) error {
		if cond == nil {
			return nil
		}
		ops := 0
		for _, op := range guardOps {
			if _, ok := cond[op]; ok {
				ops++
			}
		}
		if ops != 1 {
			return fmt.Errorf("%s has %d comparison operators; a condition has exactly one (PS-126)", where, ops)
		}
		for _, op := range []string{"in", "not_in"} {
			value, present := cond[op]
			if !present {
				continue
			}
			list, ok := value.([]any)
			if ok {
				for _, item := range list {
					switch item.(type) {
					case int, int64, uint64, float64:
					default:
						ok = false
					}
				}
			}
			if !ok {
				return fmt.Errorf("`%s` in %s is a list of numbers (PS-543)", op, where)
			}
		}
		return nil
	}
	if m := asStringMap(node); m != nil {
		name := "?"
		if n, ok := m["name"]; ok {
			name = fmt.Sprint(n)
		}
		if err := check(asStringMap(m["present_if"]), "the present_if of '"+name+"'"); err != nil {
			return err
		}
		if guard := asStringMap(m["guard"]); guard != nil {
			when, _ := guard["when"].([]any)
			for _, cond := range when {
				if err := check(asStringMap(cond), "a guard condition of '"+name+"'"); err != nil {
					return err
				}
			}
		}
		for _, key := range sortedKeys(m) {
			if key == "test_vectors" {
				continue
			}
			if err := checkSetConditions(m[key]); err != nil {
				return err
			}
		}
	} else if list, ok := node.([]any); ok {
		for _, item := range list {
			if err := checkSetConditions(item); err != nil {
				return err
			}
		}
	}
	return nil
}
