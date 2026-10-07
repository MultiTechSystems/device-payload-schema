package schema

// 0.5.2 wave 6b: field-level rules.
//
// CR-2026-056 (a length naming a preceding field, PS-464, PS-465), CR-2026-059 (optional
// fields, PS-402 to PS-405), CR-2026-062 (match on the bytes remaining, PS-414 to PS-416),
// CR-2026-065 (sentinel and out_of_range: omit, PS-427 to PS-429) and CR-2026-067
// (internal fields, PS-432 to PS-435). Each function mirrors the one of the same purpose
// in tools/schema_interpreter.py: resolve_length, optional_field_size, optional_errors,
// internal_name_errors, _sentinel_hit, _range_omits, _mark_absent and
// _internal_encode_value.

import (
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

// --- parsing -----------------------------------------------------------------

var integerText = regexp.MustCompile(`^-?\d+$`)

// parseLengthText reads a string `length`: `remaining` (PS-014), an integer written as
// text, or the name of a preceding field with or without `$` (PS-464).
func parseLengthText(f *Field, text string) {
	text = strings.TrimSpace(text)
	switch {
	case strings.EqualFold(text, "remaining"):
		f.Length = -1
	case integerText.MatchString(text):
		n, _ := strconv.Atoi(text)
		if n < 0 {
			n = -1 // a negative count is `remaining`, as resolve_length reads it
		}
		f.Length = n
	case text != "":
		f.LengthRef = strings.TrimPrefix(text, "$")
	}
}

// parseWave6bKeys reads optional, sentinel and out_of_range.
func parseWave6bKeys(f *Field, fm map[string]any) {
	if optional, ok := fm["optional"].(bool); ok {
		f.Optional = optional
	}
	if list, ok := fm["sentinel"].([]any); ok {
		for _, item := range list {
			if n, ok := intValue(item); ok {
				f.Sentinel = append(f.Sentinel, int64(n))
			}
		}
	}
	f.OutOfRangeOmit = fm["out_of_range"] == "omit"
}

// --- schema rules, checked at load --------------------------------------------

// reservedOutputNames are the names clause 7 reserves for interpreter metadata (PS-176,
// PS-433).
var reservedOutputNames = map[string]bool{"_meta": true, "_quality": true, "_warnings": true}

// internalNameError is internal_name_errors: no field may take a name reserved for
// interpreter metadata (PS-433).
func internalNameError(raw map[string]any) error {
	var walk func(node any, path string) error
	walk = func(node any, path string) error {
		if m := asStringMap(node); m != nil {
			_, hasType := m["type"]
			_, hasFields := m["fields"]
			if name, ok := m["name"].(string); ok && reservedOutputNames[name] && (hasType || hasFields) {
				return fmt.Errorf("%s: '%s' is reserved for interpreter metadata and cannot name a field (PS-433)", path, name)
			}
			for key, value := range m {
				if key == "test_vectors" || key == "definitions" {
					continue
				}
				at := key
				if path != "" {
					at = path + "." + key
				}
				if err := walk(value, at); err != nil {
					return err
				}
			}
		} else if list, ok := node.([]any); ok {
			for i, item := range list {
				if err := walk(item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
		return nil
	}
	return walk(raw, "")
}

// optionalError is optional_errors: in any field list, every field after an optional
// field is optional too (PS-404).
func optionalError(fields any, where string) error {
	list, _ := fields.([]any)
	seen := ""
	for i, raw := range list {
		f := asStringMap(raw)
		if f == nil {
			continue
		}
		name, _ := f["name"].(string)
		if name == "" {
			name = "?"
		}
		if f["optional"] == true {
			if seen == "" {
				seen = name
			}
		} else if seen != "" {
			return fmt.Errorf("%s[%d] (%s): follows optional field '%s', so it must be optional too (PS-404)",
				where, i, name, seen)
		}
		at := fmt.Sprintf("%s[%d]", where, i)
		for key, value := range f {
			switch key {
			case "fields", "trailer", "default":
				if nested, ok := value.([]any); ok {
					if err := optionalError(nested, at+"."+key); err != nil {
						return err
					}
				}
			case "match", "tlv":
				body := asStringMap(value)
				if body == nil {
					continue
				}
				for caseKey, caseBody := range asStringMap(body["cases"]) {
					if nested, ok := caseBody.([]any); ok {
						if err := optionalError(nested, fmt.Sprintf("%s.%s[%s]", at, key, caseKey)); err != nil {
							return err
						}
					}
				}
				if nested, ok := body["default"].([]any); ok {
					if err := optionalError(nested, at+"."+key+".default"); err != nil {
						return err
					}
				}
			case "flagged":
				body := asStringMap(value)
				if body == nil {
					continue
				}
				groups, _ := body["groups"].([]any)
				for g, group := range groups {
					if gm := asStringMap(group); gm != nil {
						if err := optionalError(gm["fields"], fmt.Sprintf("%s.flagged[%d]", at, g)); err != nil {
							return err
						}
					}
				}
			}
		}
	}
	return nil
}

// checkWave6bRules holds PS-404 over the top-level and port field lists, and PS-433
// over the whole document.
func checkWave6bRules(raw map[string]any) error {
	if err := optionalError(raw["fields"], "fields"); err != nil {
		return err
	}
	for port, entry := range asStringMap(raw["ports"]) {
		group := entry
		if m := asStringMap(entry); m != nil {
			group = m["fields"]
		}
		if err := optionalError(group, fmt.Sprintf("ports[%s].fields", port)); err != nil {
			return err
		}
	}
	return internalNameError(raw)
}

// checkWave6bFieldRules holds the per-field forms: a sentinel is a list of integers
// (PS-427), out_of_range is `omit` (PS-428), and a match declares exactly one
// discriminator source (PS-399, PS-416), `remaining` being only `true` (PS-414).
func checkWave6bFieldRules(field map[string]any, at string) error {
	if sentinel, ok := field["sentinel"]; ok {
		list, isList := sentinel.([]any)
		if !isList || len(list) == 0 {
			return fmt.Errorf("%s: sentinel must be a list of integers (PS-427)", at)
		}
		for _, item := range list {
			if _, isInt := intValue(item); !isInt {
				return fmt.Errorf("%s: sentinel must be a list of integers, not %v (PS-427)", at, item)
			}
		}
	}
	if mode, ok := field["out_of_range"]; ok && mode != "omit" {
		return fmt.Errorf("%s: out_of_range must be omit, not %v (PS-428)", at, mode)
	}
	if match := asStringMap(field["match"]); match != nil {
		sources := 0
		for _, key := range []string{"field", "length", "remaining"} {
			if _, has := match[key]; has {
				sources++
			}
		}
		if sources != 1 {
			return fmt.Errorf("%s.match: a match must declare exactly one of 'field', 'length' and 'remaining' (PS-399, PS-416)", at)
		}
		if remaining, has := match["remaining"]; has && remaining != true {
			return fmt.Errorf("%s.match: a match's remaining must be true (PS-414)", at)
		}
	}
	return nil
}

// --- decoding ------------------------------------------------------------------

// absentSentinel marks a field whose reading was a sentinel (PS-427): it is absent, and
// unlike `omitted` it is recorded in `_quality` where that is produced.
var absentSentinel = &struct{ name string }{"absent: sentinel"}

// resolveLengthRef is the PS-464 half of resolve_length: the count a `length` naming a
// preceding field stands for. An unresolved name is an error naming it (PS-465).
func resolveLengthRef(field Field, ctx *DecodeContext) (int, error) {
	name := field.LengthRef
	value, bound := ctx.Variables[name]
	if !bound {
		return 0, fmt.Errorf("field '%s': length names '%s', which is not a field decoded before this one (PS-465)",
			field.Name, name)
	}
	_, isBool := value.(bool)
	n, isNumber := toFloat64(value)
	if isBool || !isNumber || n < 0 || n != math.Trunc(n) {
		return 0, fmt.Errorf("field '%s': length names '%s', whose value %v is not a byte count (PS-464)",
			field.Name, name, value)
	}
	return int(n), nil
}

// optionalFieldSize is optional_field_size: the bytes an optional field takes, or 0
// where its size depends on the payload. An object is sized as its fields sum.
func optionalFieldSize(field Field) int {
	if field.Type == TypeObjectLower || field.Type == TypeObject {
		return fixedElementSize(field.Fields)
	}
	return fixedElementSize([]Field{field})
}

// sentinelHit is _sentinel_hit: the integer read, before any modifier and before any
// encoding is decoded, is one of the field's sentinels (PS-427). raw is that integer.
func sentinelHit(field Field, raw any) bool {
	if len(field.Sentinel) == 0 {
		return false
	}
	for _, s := range field.Sentinel {
		switch v := raw.(type) {
		case uint64:
			if s >= 0 && v == uint64(s) {
				return true
			}
		case int64:
			if v == s {
				return true
			}
		case int:
			if int64(v) == s {
				return true
			}
		case float64:
			// A bit range's value, which extractRange gives as a float of an integer.
			if v == math.Trunc(v) && v == float64(s) {
				return true
			}
		}
	}
	return false
}

// rangeOmits is _range_omits: `out_of_range: omit` and a value outside `valid_range`
// (PS-428).
func rangeOmits(field Field, value any) bool {
	if !field.OutOfRangeOmit || len(field.ValidRange) != 2 {
		return false
	}
	if _, isBool := value.(bool); isBool {
		return false
	}
	n, ok := toFloat64(value)
	if !ok {
		return false
	}
	return n < field.ValidRange[0] || n > field.ValidRange[1]
}

// markAbsent is _mark_absent: a reading omitted under PS-427 or PS-428 is recorded in
// `_quality` where `_quality` is produced. A field declaring valid_range produces it
// itself; otherwise the mark waits for the end of the decode to learn whether anything
// else did. Inside a flagged group the mark always waits, as the reference does.
func (ctx *DecodeContext) markAbsent(field Field, why string) {
	if len(field.ValidRange) > 0 && ctx.inFlagged == 0 {
		ctx.Quality[field.Name] = why
		return
	}
	if ctx.pendingAbsent == nil {
		ctx.pendingAbsent = map[string]string{}
	}
	ctx.pendingAbsent[field.Name] = why
}

// settleQuality joins the readings omitted under PS-427 and PS-428 to `_quality`, where
// it is produced, and only there.
func (ctx *DecodeContext) settleQuality() {
	if len(ctx.Quality) == 0 {
		return
	}
	for name, why := range ctx.pendingAbsent {
		if _, set := ctx.Quality[name]; !set {
			ctx.Quality[name] = why
		}
	}
}

// --- encoding ------------------------------------------------------------------

// internalEncodeValue is _internal_encode_value: what an internal field that reads
// payload bytes writes (PS-434). Its `value`; else the input's value under its name;
// else an error naming the field. This wrote zero, which reads back as a value the
// device never sent wherever the field was not padding.
func internalEncodeValue(field Field, data map[string]any) (any, error) {
	if field.Value != nil {
		return field.Value, nil
	}
	if v, ok := data[field.Name]; ok {
		return v, nil
	}
	return nil, fmt.Errorf("internal field '%s' reads payload bytes and declares no value, and the input does not supply it (PS-434)",
		field.Name)
}

// encodeSentinel writes a field's first sentinel as the raw integer the decode compared
// (PS-427): no modifier, lookup or encoding is reversed, since the sentinel was matched
// before any of them applied.
func encodeSentinel(field Field, ctx *EncodeContext) error {
	raw := field
	raw.Mult, raw.Div, raw.Add = nil, nil, nil
	raw.Transform, raw.Modifiers = nil, nil
	raw.Lookup, raw.LookupArray, raw.LookupDefault = nil, nil, nil
	raw.Encoding = ""
	return encodeField(raw, float64(field.Sentinel[0]), ctx)
}
