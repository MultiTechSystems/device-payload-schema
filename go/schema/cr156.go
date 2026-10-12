package schema

// CR-2026-156 (PS-567 to PS-569): `sampled` on a repeat.
//
// `sampled` says a repeat's elements are samples of one measurement taken at a fixed
// interval. It is an annotation: decoding and encoding ignore it. This file holds the
// load check (PS-569) and the `_meta` entry (PS-568); both mirror sampled_errors and
// sampled_meta in tools/schema_interpreter.py, whose wording is the spec prototype's
// check_sampled. A `$` interval must name a field the repeat can see, which
// checkReferences holds to PS-539 (`interval` is one of its reference keys).

import (
	"fmt"
	"strconv"
	"strings"
)

// sampledMeta is sampled_meta: the declaration with `order` and `at` filled in, a `$name`
// interval written {"field": name}.
func sampledMeta(sampled map[string]any) map[string]any {
	interval := sampled["interval"]
	if text, ok := interval.(string); ok {
		interval = map[string]any{"field": strings.TrimPrefix(text, "$")}
	}
	order, ok := sampled["order"]
	if !ok {
		order = "newest_first"
	}
	at, ok := sampled["at"]
	if !ok {
		at = "rx_time"
	}
	return map[string]any{"interval": interval, "order": order, "at": at}
}

// sampledRepr writes a scalar, a list or a mapping as Python's repr does, so the message names
// the offending value as the reference's does.
func sampledRepr(v any) string {
	switch t := v.(type) {
	case nil:
		return "None"
	case string:
		return "'" + t + "'"
	case bool:
		if t {
			return "True"
		}
		return "False"
	case float64:
		s := strconv.FormatFloat(t, 'g', -1, 64)
		if !strings.ContainsAny(s, ".eEn") {
			s += ".0"
		}
		return s
	case []any:
		parts := make([]string, len(t))
		for i, item := range t {
			parts[i] = sampledRepr(item)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	}
	if m := asStringMap(v); m != nil {
		parts := make([]string, 0, len(m))
		for _, k := range sortedKeys(m) {
			parts = append(parts, sampledRepr(k)+": "+sampledRepr(m[k]))
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return fmt.Sprintf("%v", v)
}

// checkSampled is sampled_errors: the first declaration, in source order of the header,
// the fields, the ports and the definitions, that PS-569 rejects.
func checkSampled(raw map[string]any, order keyOrder) error {
	times := map[string]bool{}
	if metadata := asStringMap(raw["metadata"]); metadata != nil {
		if list, ok := metadata["timestamps"].([]any); ok {
			for _, item := range list {
				if ts := asStringMap(item); ts != nil {
					if mode := ts["mode"]; mode == "unix_epoch" || mode == "iso8601" {
						if name, ok := ts["name"].(string); ok {
							times[name] = true
						}
					}
				}
			}
		}
	}
	bad := func(field map[string]any, why string) error {
		name := "None"
		if s, ok := field["name"].(string); ok {
			name = s
		}
		return fmt.Errorf("Field '%s': sampled %s (PS-569)", name, why)
	}
	check := func(field map[string]any) error {
		sampled := field["sampled"]
		if field["type"] != "repeat" {
			return bad(field, fmt.Sprintf("is defined only on a repeat, not on %s", fmt.Sprint(field["type"])))
		}
		sm := asStringMap(sampled)
		if sm == nil {
			return bad(field, "needs an interval")
		}
		interval, present := sm["interval"]
		if !present {
			return bad(field, "needs an interval")
		}
		valid := false
		switch t := interval.(type) {
		case string:
			valid = strings.HasPrefix(t, "$")
		case bool:
		default:
			if n, ok := toFloat64(interval); ok {
				valid = n > 0
			} else if im := asStringMap(interval); im != nil && len(im) == 1 {
				_, valid = im["configured"].(string)
			}
		}
		if !valid {
			return bad(field, fmt.Sprintf("interval %s is not a number of seconds above 0, a $ reference or {configured: name}", sampledRepr(interval)))
		}
		if o, ok := sm["order"]; ok && o != "newest_first" && o != "oldest_first" {
			return bad(field, fmt.Sprintf("order %s is not newest_first or oldest_first", sampledRepr(o)))
		}
		if at, ok := sm["at"]; ok {
			name, isString := at.(string)
			if !(isString && (name == "rx_time" || times[name])) {
				return bad(field, fmt.Sprintf("at %s is neither rx_time nor a unix_epoch or iso8601 timestamps entry", sampledRepr(at)))
			}
		}
		return nil
	}
	var walk func(node any) error
	walk = func(node any) error {
		if list, ok := node.([]any); ok {
			for _, item := range list {
				if err := walk(item); err != nil {
					return err
				}
			}
			return nil
		}
		m := asStringMap(node)
		if m == nil {
			return nil
		}
		if _, has := m["sampled"]; has && m["type"] != nil {
			if err := check(m); err != nil {
				return err
			}
		}
		for _, key := range order.keys(node) {
			if key == "test_vectors" {
				continue
			}
			if err := walk(m[key]); err != nil {
				return err
			}
		}
		return nil
	}
	for _, key := range []string{"header", "fields", "ports", "definitions"} {
		if err := walk(raw[key]); err != nil {
			return err
		}
	}
	return nil
}
