// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

// The interpreter output's `_meta` (Clause 7: PS-175, PS-177, PS-178, PS-180, PS-181,
// PS-340 to PS-342, PS-371 to PS-376; CR-2026-088 PS-480/481; CR-2026-095 PS-489;
// CR-2026-096 PS-490 to PS-497).
//
// A port of the `_meta` section of tools/schema_interpreter.py: each function here
// mirrors the module function of the same purpose (meta_type, meta_declarations,
// meta_reference, field_meta, normalise_dev_eui, rx_time_seconds,
// meta_declaration_errors) and SchemaInterpreter.interpret. The entries are derived
// from the declarations as written, so they work on each field's raw mapping
// (Field.decl) rather than on the parsed Field, which has already canonicalised and
// dropped what `_meta` reports.
//
// A Go map does not keep its source order, and three things here depend on it: a
// lookup's labels are listed in schema order (PS-375), the first declaration of a name
// across match or tlv cases is the one a nested entry describes, and PS-491 reports the
// first disagreement found. keyOrder carries that order, recovered from the YAML node
// tree once at load.

import (
	"fmt"
	"math/big"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// InputContext is what an interpreter integration knows about an uplink beyond its
// bytes (PS-495): the TS013 input's fPort and recvTime, and the device's EUI.
//
// FPort nil means no port; it selects the port entry exactly as DecodeWithPort does.
// RecvTime is an ISO 8601 string or a number of Unix seconds; nil means not supplied.
// DevEUI "" means not supplied.
type InputContext struct {
	FPort    *int
	RecvTime any
	DevEUI   string
}

// Interpret is the interpreter output: the decoded result with `_meta` added (PS-174,
// PS-175, PS-180). Decode and DecodeWithPort are unchanged; a generated codec never
// carries `_meta` (PS-467).
//
// A failed decode has no `_meta` (PS-497) and returns the decode's error. A devEUI or
// recvTime that is present but malformed is an error too, and produces no `_meta`.
// Decode warnings are reported under `_warnings`, as Decode reports them.
func (s *Schema) Interpret(payload []byte, in InputContext) (map[string]any, error) {
	producers := map[string]map[string]any{}
	var result map[string]any
	var err error
	if in.FPort != nil {
		result, err = s.decodeWithPort(payload, *in.FPort, "", producers)
	} else {
		result, err = s.decode(payload, producers)
	}
	if err != nil {
		return nil, err
	}

	meta := map[string]any{}
	if s.rawName != nil {
		meta["schema"] = s.rawName
	}
	if s.hasVersion {
		meta["version"] = s.rawVersion
	}
	// PS-496, PS-495: a context present but malformed is refused, not dropped - a
	// missing device_eui would read as "not supplied".
	if in.DevEUI != "" {
		eui, ok := normaliseDevEUI(in.DevEUI)
		if !ok {
			return nil, fmt.Errorf("devEUI %s is not 16 hexadecimal digits (PS-496)", pyRepr(in.DevEUI))
		}
		meta["device_eui"] = eui
	}
	if in.RecvTime != nil {
		rx, ok := rxTimeSeconds(in.RecvTime)
		if !ok {
			return nil, fmt.Errorf("recvTime %s is not an ISO 8601 time or a number of seconds (PS-495)", pyRepr(in.RecvTime))
		}
		meta["rx_time"] = rx
	}
	if in.FPort != nil {
		meta["fPort"] = *in.FPort // PS-340, PS-342: as supplied
	}

	var selected []Field
	if in.FPort != nil {
		selected, _ = s.ResolveFields(*in.FPort)
	} else {
		selected = s.Fields
	}
	declared := s.metaDeclarations(fieldDecls(selected))
	fields := map[string]any{}
	for key := range result {
		if strings.HasPrefix(key, "_") {
			continue // `_quality`, `_warnings`
		}
		source := producers[key]
		if source == nil {
			source = declared.get(key)
		}
		if source == nil {
			fields[key] = map[string]any{}
		} else {
			fields[key] = s.fieldMeta(source, nil)
		}
	}
	meta["fields"] = fields

	out := make(map[string]any, len(result)+1)
	for k, v := range result {
		out[k] = v
	}
	out["_meta"] = meta
	return out, nil
}

// produced records the declaration about to report key (PS-490). The last one decoded
// is the one whose value is reported, so the last write wins. Only the top level is
// recorded: an object's members and a repeat's elements are described by the nested
// entries of their declaration (PS-371, PS-481).
func (ctx *DecodeContext) produced(decl map[string]any, key string) {
	if ctx.metaProducers == nil || ctx.metaDepth > 0 || decl == nil {
		return
	}
	if key == "" || strings.HasPrefix(key, "_") {
		return
	}
	ctx.metaProducers[key] = decl
}

// decodeFieldProduced is decodeField for a member of a field list, recording the field
// as the producer of its name and counting the depth of an object or a repeat, as the
// reference's _decode_field does.
func (ctx *DecodeContext) decodeFieldProduced(field Field) (any, error) {
	if ctx.metaProducers == nil {
		return decodeField(field, ctx)
	}
	ctx.produced(field.decl, field.Name)
	if t, _ := field.decl["type"].(string); t == "object" || t == "repeat" {
		ctx.metaDepth++
		defer func() { ctx.metaDepth-- }()
	}
	return decodeField(field, ctx)
}

func fieldDecls(fields []Field) []any {
	out := make([]any, 0, len(fields))
	for _, f := range fields {
		if f.decl != nil {
			out = append(out, f.decl)
		}
	}
	return out
}

// --- the entries ---

// metaTypeAliases are alias type names written as their canonical name (PS-493).
var metaTypeAliases = func() map[string]string {
	out := map[string]string{}
	for _, n := range []int{8, 16, 24, 32, 64} {
		out[fmt.Sprintf("uint%d", n)] = fmt.Sprintf("u%d", n)
		out[fmt.Sprintf("int%d", n)] = fmt.Sprintf("s%d", n)
		out[fmt.Sprintf("i%d", n)] = fmt.Sprintf("s%d", n)
	}
	return out
}()

// metaType is an entry's `type` (PS-493): the declared type, an alias by its canonical
// name, a bit range as written. A `tlv` with `merge: false` is `tlv` (its `channels` key). A field with
// no type is a schema error (PS-011, PS-441), so none is invented: "" leaves it out.
func metaType(decl map[string]any) string {
	if declared, ok := decl["type"].(string); ok {
		if canonical, ok := metaTypeAliases[declared]; ok {
			return canonical
		}
		return declared
	}
	if asStringMap(decl["tlv"]) != nil {
		return "tlv"
	}
	return ""
}

// declarations is reported name -> declaration, in first-declared order.
type declarations struct {
	names []string
	decls map[string]map[string]any
}

func (d *declarations) get(name string) map[string]any { return d.decls[name] }

func (d *declarations) setDefault(name string, decl map[string]any) {
	if _, ok := d.decls[name]; ok {
		return
	}
	d.names = append(d.names, name)
	d.decls[name] = decl
}

// metaDeclarations mirrors meta_declarations: reported name -> declaration for one
// level of output, first declaration first. A construct that merges into its parent
// (match cases and default, tlv cases, flagged groups, byte_group) contributes its
// fields at this level; an object or a repeat contributes its own name. Internal fields
// (PS-494) and name_from fields (PS-492) have no entry.
func (s *Schema) metaDeclarations(fields any) *declarations {
	found := &declarations{decls: map[string]map[string]any{}}
	var visit func(items any)
	visitCases := func(cases any) {
		if m := asStringMap(cases); m != nil {
			for _, key := range s.keyOrder.keys(cases) {
				if body, ok := m[key].([]any); ok {
					visit(body)
				}
			}
		}
	}
	visit = func(items any) {
		list, ok := items.([]any)
		if !ok {
			return
		}
		for _, item := range list {
			f := asStringMap(item)
			if f == nil {
				continue
			}
			if group, present := f["byte_group"]; present && group != nil {
				if gm := asStringMap(group); gm != nil {
					visit(gm["fields"])
				} else {
					visit(group)
				}
			}
			if flagged := asStringMap(f["flagged"]); flagged != nil {
				if groups, ok := flagged["groups"].([]any); ok {
					for _, g := range groups {
						if gm := asStringMap(g); gm != nil {
							visit(gm["fields"])
						}
					}
				}
			}
			if match := asStringMap(f["match"]); match != nil {
				visitCases(match["cases"])
				if def, ok := match["default"].([]any); ok {
					visit(def)
				}
				if label, ok := match["name"].(string); ok && !strings.HasPrefix(label, "_") {
					length := 1
					if raw, present := match["length"]; present {
						if n, ok := toInt(raw); ok {
							length = n
						}
					}
					found.setDefault(label, map[string]any{"name": label, "type": fmt.Sprintf("u%d", 8*length)})
				}
			}
			if t, _ := f["type"].(string); t == "match" {
				visitCases(f["cases"])
			}
			if tlv := asStringMap(f["tlv"]); tlv != nil {
				if tlv["merge"] == false {
					// Its entries are reported as a list under the fixed key `channels`
					// (Clause 4), whose entry is `tlv` (CR-2026-097, PS-493). A tlv has
					// no name of its own (PS-448).
					found.setDefault("channels", f)
					continue
				}
				visitCases(tlv["cases"])
				continue
			}
			name, ok := f["name"].(string)
			if !ok || strings.HasPrefix(name, "_") || truthy(f["name_from"]) {
				continue
			}
			if _, hasMatch := f["match"]; hasMatch {
				if _, hasType := f["type"]; !hasType {
					continue
				}
			}
			found.setDefault(name, f)
		}
	}
	visit(fields)
	return found
}

// metaReference mirrors meta_reference: a per-element `unit`, `ipso.instance` or
// `identity` as PS-375 lists it - the repeat's index as {index, count}, or an element
// field as {field, values}, open where a lookup default carries ${value} (PS-489). A
// literal is itself.
func (s *Schema) metaReference(value any, repeat map[string]any, bare bool) any {
	text, isString := value.(string)
	if repeat == nil || !isString {
		return value
	}
	if !(bare || strings.HasPrefix(text, "$")) {
		return value
	}
	name := strings.TrimPrefix(text, "$")
	if index, ok := repeat["index"].(string); ok && name == index {
		count := repeat["count"]
		if !isPlainInt(count) {
			count = repeat["max"]
		}
		return map[string]any{"index": name, "count": count}
	}
	target := map[string]any{}
	if members, ok := repeat["fields"].([]any); ok {
		for _, member := range members {
			if m := asStringMap(member); m != nil && m["name"] == name {
				target = m
				break
			}
		}
	}
	values := []any{}
	lookup := target["lookup"]
	if lm := asStringMap(lookup); lm != nil {
		for _, key := range s.keyOrder.keys(lookup) {
			if key != "default" {
				values = append(values, lm[key])
			}
		}
		if def, present := lm["default"]; present {
			values = append(values, def)
		}
	} else if list, ok := lookup.([]any); ok {
		values = append(values, list...)
	} else if v, present := target["value"]; present {
		values = []any{v}
	}
	listing := map[string]any{"field": name, "values": values}
	if lm := asStringMap(lookup); lm != nil {
		if def, ok := lm["default"].(string); ok && strings.Contains(def, lookupValueToken) {
			listing["open"] = true // PS-489
		}
	}
	return listing
}

// fieldMeta mirrors field_meta: one `_meta.fields` entry from one declaration (PS-178,
// PS-493, PS-480, PS-371, PS-481). repeat is the repeat whose elements the field belongs
// to, for the per-element references of PS-373 and PS-375.
func (s *Schema) fieldMeta(decl map[string]any, repeat map[string]any) map[string]any {
	entry := map[string]any{}
	declaredType := metaType(decl)
	if declaredType != "" {
		entry["type"] = declaredType
	}
	senml := asStringMap(decl["senml"])
	if senml == nil {
		senml = map[string]any{}
	}
	unit := senml["unit"] // PS-178, no conversion
	if !truthy(unit) {
		unit = decl["unit"]
	}
	if unit != nil {
		entry["unit"] = s.metaReference(unit, repeat, false)
	}
	if ipso := asStringMap(decl["ipso"]); ipso != nil {
		if object, present := ipso["object"]; present {
			instance, ok := ipso["instance"]
			if !ok {
				instance = 0
			}
			resource, ok := ipso["resource"]
			if !ok {
				resource = 5700
			}
			entry["ipso"] = map[string]any{
				"object":   object,
				"instance": s.metaReference(instance, repeat, false),
				"resource": resource,
			}
		}
	}
	if name, ok := senml["name"].(string); ok {
		entry["senml"] = map[string]any{"name": name} // PS-480; a template, PS-373
	}
	if truthy(decl["description"]) {
		entry["description"] = decl["description"]
	}
	switch declaredType {
	case "repeat":
		elements := map[string]any{} // PS-371
		members := s.metaDeclarations(decl["fields"])
		for _, name := range members.names {
			elements[name] = s.fieldMeta(members.decls[name], decl)
		}
		entry["elements"] = elements
		if identity, present := decl["identity"]; present {
			entry["identity"] = s.metaReference(identity, decl, true)
		}
	case "object":
		nested := map[string]any{} // PS-481
		members := s.metaDeclarations(decl["fields"])
		for _, name := range members.names {
			nested[name] = s.fieldMeta(members.decls[name], nil)
		}
		entry["fields"] = nested
	}
	return jsonSafe(entry).(map[string]any)
}

// --- the input context ---

var devEUIPattern = regexp.MustCompile(`^[0-9a-f]{16}$`)

// normaliseDevEUI is PS-496: 16 lower-case hexadecimal digits, `-`, `:` and spaces
// removed. ok is false where the input is not a device EUI once normalised.
func normaliseDevEUI(text string) (string, bool) {
	eui := strings.ToLower(strings.NewReplacer("-", "", ":", "", " ", "").Replace(text))
	if !devEUIPattern.MatchString(eui) {
		return "", false
	}
	return eui, true
}

var recvTimePattern = regexp.MustCompile(
	`^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|z|[+-]\d{2}:?\d{2})?$`)

// rxTimeSeconds is PS-177, PS-495: recvTime as numeric Unix seconds in UTC.
//
// A number is taken as seconds already. An ISO 8601 string keeps its milliseconds: the
// fraction is rounded half to even at three digits, in decimal, and a whole number of
// seconds is an integer (int64). Otherwise "<seconds>.<3 digits>" is read as one
// decimal, so the result is the same double in every implementation.
func rxTimeSeconds(recv any) (any, bool) {
	switch v := recv.(type) {
	case bool:
		return nil, false
	case int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64, float32, float64:
		return v, true
	case string:
		return parseRecvTime(v)
	}
	return nil, false
}

func parseRecvTime(text string) (any, bool) {
	m := recvTimePattern.FindStringSubmatch(strings.TrimSpace(text))
	if m == nil {
		return nil, false
	}
	parts := make([]int64, 6)
	for i := range parts {
		n, err := strconv.ParseInt(m[i+1], 10, 64)
		if err != nil {
			return nil, false
		}
		parts[i] = n
	}
	year, month := parts[0], parts[1]
	// calendar.timegm refuses a month or year that datetime.date does, and carries any
	// other field over arithmetically - as this does.
	if year < 1 || month < 1 || month > 12 {
		return nil, false
	}
	seconds := time.Date(int(year), time.Month(month), 1, 0, 0, 0, 0, time.UTC).Unix() +
		(parts[2]-1)*86400 + parts[3]*3600 + parts[4]*60 + parts[5]
	if zone := m[8]; zone != "" && zone != "Z" && zone != "z" {
		sign := int64(1)
		if zone[0] == '-' {
			sign = -1
		}
		digits := strings.ReplaceAll(zone[1:], ":", "")
		hours, _ := strconv.ParseInt(digits[:2], 10, 64)
		minutes, _ := strconv.ParseInt(digits[2:], 10, 64)
		seconds -= sign * (hours*3600 + minutes*60)
	}

	// Round the fraction to milliseconds, half to even, on its decimal digits.
	fraction := m[7]
	millis := int64(0)
	if fraction != "" {
		padded := fraction
		for len(padded) < 3 {
			padded += "0"
		}
		millis, _ = strconv.ParseInt(padded[:3], 10, 64)
		rest := strings.TrimRight(padded[3:], "0")
		switch {
		case rest == "":
		case rest[0] > '5' || (rest[0] == '5' && len(rest) > 1):
			millis++
		case rest[0] == '5':
			if millis%2 == 1 {
				millis++
			}
		}
	}
	total := seconds*1000 + millis
	if total%1000 == 0 {
		return total / 1000, true
	}
	sign := ""
	abs := total
	if abs < 0 {
		sign, abs = "-", -abs
	}
	value, err := strconv.ParseFloat(fmt.Sprintf("%s%d.%03d", sign, abs/1000, abs%1000), 64)
	if err != nil {
		return nil, false
	}
	return value, true
}

// pyRepr writes a value as Python's %r does, for the two context errors, whose text the
// reference fixes.
func pyRepr(v any) string {
	switch t := v.(type) {
	case string:
		quote := "'"
		if strings.Contains(t, "'") && !strings.Contains(t, `"`) {
			quote = `"`
		}
		var b strings.Builder
		b.WriteString(quote)
		for _, r := range t {
			switch {
			case r == '\\':
				b.WriteString(`\\`)
			case string(r) == quote:
				b.WriteString(`\` + quote)
			case r == '\n':
				b.WriteString(`\n`)
			case r == '\r':
				b.WriteString(`\r`)
			case r == '\t':
				b.WriteString(`\t`)
			case r < 0x20 || r == 0x7f:
				b.WriteString(fmt.Sprintf(`\x%02x`, r))
			default:
				b.WriteRune(r)
			}
		}
		b.WriteString(quote)
		return b.String()
	case bool:
		if t {
			return "True"
		}
		return "False"
	case nil:
		return "None"
	}
	return fmt.Sprintf("%v", v)
}

// --- PS-491 at load ---

// metaAgreementKey is what PS-491 requires one reported name's declarations to agree
// on, each part written so that two declarations agree exactly when the reference's
// repr() of them does.
func metaAgreementKey(decl map[string]any) [3]string {
	repr := func(v any) string { return fmt.Sprintf("%T:%v", v, v) }
	unit := repr(decl["unit"])
	senml := repr(decl["senml"])
	if m := asStringMap(decl["senml"]); m != nil {
		pairs := make([]string, 0, len(m))
		for k, v := range m {
			pairs = append(pairs, fmt.Sprintf("%q=%q", k, fmt.Sprintf("%v", v)))
		}
		sort.Strings(pairs)
		senml = "senml{" + strings.Join(pairs, ",") + "}"
	}
	ipso := repr(decl["ipso"])
	if m := asStringMap(decl["ipso"]); m != nil {
		instance, ok := m["instance"]
		if !ok {
			instance = 0
		}
		resource, ok := m["resource"]
		if !ok {
			resource = 5700
		}
		ipso = "ipso(" + repr(m["object"]) + "," + repr(instance) + "," + repr(resource) + ")"
	}
	return [3]string{unit, senml, ipso}
}

// checkMetaDeclarations is PS-491 (meta_declaration_errors in the reference): the
// declarations of one reported name in one field list must agree on unit, senml and
// ipso. A field list is the top level, each port's, and each object's or repeat's
// members; match and tlv cases, flagged groups and byte_groups merge into theirs, and a
// merge:false tlv's cases together are one list. type, lookup and description may
// differ. The first disagreement found, in the reference's order, is the error.
func checkMetaDeclarations(raw map[string]any, order keyOrder) error {
	var errors []string
	var check func(fields []any, where string)
	check = func(fields []any, where string) {
		var names []string
		seen := map[string][]map[string]any{}
		var visit func(items any)
		visitCases := func(cases any, each func([]any)) {
			if m := asStringMap(cases); m != nil {
				for _, key := range order.keys(cases) {
					if body, ok := m[key].([]any); ok {
						each(body)
					}
				}
			}
		}
		visitList := func(body []any) { visit(body) }
		visit = func(items any) {
			list, ok := items.([]any)
			if !ok {
				return
			}
			for _, item := range list {
				f := asStringMap(item)
				if f == nil {
					continue
				}
				if group, present := f["byte_group"]; present && group != nil {
					if gm := asStringMap(group); gm != nil {
						visit(gm["fields"])
					} else {
						visit(group)
					}
				}
				if flagged := asStringMap(f["flagged"]); flagged != nil {
					if groups, ok := flagged["groups"].([]any); ok {
						for _, g := range groups {
							if gm := asStringMap(g); gm != nil {
								visit(gm["fields"])
							}
						}
					}
				}
				if match := asStringMap(f["match"]); match != nil {
					visitCases(match["cases"], visitList)
					if def, ok := match["default"].([]any); ok {
						visit(def)
					}
				}
				if t, _ := f["type"].(string); t == "match" {
					visitCases(f["cases"], visitList)
				}
				if tlv := asStringMap(f["tlv"]); tlv != nil {
					if tlv["merge"] == false {
						var members []any
						visitCases(tlv["cases"], func(body []any) { members = append(members, body...) })
						name, present := f["name"]
						if !present {
							name = "tlv"
						}
						check(members, fmt.Sprintf("%s/%v", where, name))
					} else {
						visitCases(tlv["cases"], visitList)
					}
					continue
				}
				if t, _ := f["type"].(string); t == "object" || t == "repeat" {
					if nested, ok := f["fields"].([]any); ok {
						name, present := f["name"]
						if !present {
							name = "?"
						}
						check(nested, fmt.Sprintf("%s/%v", where, name))
					}
				}
				name, ok := f["name"].(string)
				if ok && !strings.HasPrefix(name, "_") && !truthy(f["name_from"]) {
					if _, known := seen[name]; !known {
						names = append(names, name)
					}
					seen[name] = append(seen[name], f)
				}
			}
		}
		visit(fields)
		for _, name := range names {
			decls := seen[name]
			var differing []string
			for i, label := range []string{"unit", "senml", "ipso"} {
				distinct := map[string]bool{}
				for _, d := range decls {
					distinct[metaAgreementKey(d)[i]] = true
				}
				if len(distinct) > 1 {
					differing = append(differing, label)
				}
			}
			if len(differing) > 0 {
				errors = append(errors, fmt.Sprintf(
					"Field '%s' is declared %d times in %s and its declarations differ in "+
						"%s; the declarations of one reported name must agree on unit, senml "+
						"and ipso (PS-491)", name, len(decls), where, strings.Join(differing, ", ")))
			}
		}
	}

	if fields, ok := raw["fields"].([]any); ok {
		check(fields, "fields")
	}
	if ports := asStringMap(raw["ports"]); ports != nil {
		for _, key := range order.keys(raw["ports"]) {
			entry := ports[key]
			group := entry
			if m := asStringMap(entry); m != nil {
				group = m["fields"]
			}
			if list, ok := group.([]any); ok {
				check(list, "port "+key)
			}
		}
	}
	if len(errors) > 0 {
		return fmt.Errorf("%s", errors[0])
	}
	return nil
}

// --- helpers ---

// truthy is Python's truth value for a decoded YAML value.
func truthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case int:
		return t != 0
	case int64:
		return t != 0
	case uint64:
		return t != 0
	case float64:
		return t != 0
	case []any:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	case map[any]any:
		return len(t) > 0
	}
	return true
}

// isPlainInt is Python's isinstance(v, int) and not a bool.
func isPlainInt(v any) bool {
	switch v.(type) {
	case int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64, *big.Int:
		return true
	}
	return false
}

// jsonSafe turns map[any]any, which encoding/json cannot write, into map[string]any.
func jsonSafe(v any) any {
	switch t := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, val := range t {
			out[k] = jsonSafe(val)
		}
		return out
	case map[any]any:
		out := make(map[string]any, len(t))
		for k, val := range t {
			out[fmt.Sprintf("%v", k)] = jsonSafe(val)
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, val := range t {
			out[i] = jsonSafe(val)
		}
		return out
	}
	return v
}

// --- source order ---

// keyOrder maps a decoded mapping, by identity, to its keys in source order, written as
// asStringMap writes them.
type keyOrder map[uintptr][]string

func mapIdentity(m any) (uintptr, bool) {
	v := reflect.ValueOf(m)
	if v.Kind() != reflect.Map || v.IsNil() {
		return 0, false
	}
	return v.Pointer(), true
}

// keys returns m's keys in source order. A mapping whose order is unknown - a schema
// read as JSON that YAML could not parse - falls back to its keys sorted, numbers first
// in numeric order.
func (o keyOrder) keys(m any) []string {
	view := asStringMap(m)
	if view == nil {
		return nil
	}
	var out []string
	seen := map[string]bool{}
	if id, ok := mapIdentity(m); ok {
		for _, key := range o[id] {
			if _, present := view[key]; present && !seen[key] {
				out = append(out, key)
				seen[key] = true
			}
		}
	}
	var rest []string
	for key := range view {
		if !seen[key] {
			rest = append(rest, key)
		}
	}
	sort.Slice(rest, func(i, j int) bool {
		a, errA := strconv.ParseFloat(rest[i], 64)
		b, errB := strconv.ParseFloat(rest[j], 64)
		switch {
		case errA == nil && errB == nil:
			return a < b
		case errA == nil:
			return true
		case errB == nil:
			return false
		}
		return rest[i] < rest[j]
	})
	return append(out, rest...)
}

// buildKeyOrder walks the decoded (and $ref-expanded) document beside its YAML node
// tree, splicing each `$ref` in the node tree as expandRawRefs spliced it in the
// document, and records every mapping's key order.
func buildKeyOrder(raw map[string]any, root *yaml.Node) keyOrder {
	order := keyOrder{}
	node := derefNode(root)
	if node == nil || node.Kind != yaml.MappingNode {
		return order
	}
	definitions := map[string]*yaml.Node{}
	if defs := mappingValue(node, "definitions"); defs != nil && defs.Kind == yaml.MappingNode {
		for i := 0; i+1 < len(defs.Content); i += 2 {
			if fields := mappingValue(derefNode(defs.Content[i+1]), "fields"); fields != nil {
				definitions[defs.Content[i].Value] = fields
			}
		}
	}
	var expandSeq func(seq *yaml.Node, depth int) []*yaml.Node
	expandSeq = func(seq *yaml.Node, depth int) []*yaml.Node {
		var out []*yaml.Node
		for _, item := range seq.Content {
			item = derefNode(item)
			if item != nil && item.Kind == yaml.MappingNode {
				if ref := mappingValue(item, "$ref"); ref != nil {
					name := strings.TrimPrefix(ref.Value, refPrefix)
					if fields := definitions[name]; fields != nil && depth < 32 && fields.Kind == yaml.SequenceNode {
						out = append(out, expandSeq(fields, depth+1)...)
					}
					continue
				}
			}
			out = append(out, item)
		}
		return out
	}
	var walk func(value any, node *yaml.Node, inDefinitions bool)
	walk = func(value any, node *yaml.Node, inDefinitions bool) {
		node = derefNode(node)
		if node == nil {
			return
		}
		switch value.(type) {
		case map[string]any, map[any]any:
			if node.Kind != yaml.MappingNode {
				return
			}
			view := asStringMap(value)
			var keys []string
			for i := 0; i+1 < len(node.Content); i += 2 {
				keyNode := node.Content[i]
				if keyNode.Tag == "!!merge" {
					continue
				}
				var decoded any
				if err := keyNode.Decode(&decoded); err != nil {
					continue
				}
				key := fmt.Sprintf("%v", decoded)
				keys = append(keys, key)
				if child, present := view[key]; present {
					walk(child, node.Content[i+1], inDefinitions)
				}
			}
			if id, ok := mapIdentity(value); ok {
				order[id] = keys
			}
		case []any:
			if node.Kind != yaml.SequenceNode {
				return
			}
			items := node.Content
			if !inDefinitions {
				items = expandSeq(node, 0)
			}
			list := value.([]any)
			for i := 0; i < len(list) && i < len(items); i++ {
				walk(list[i], items[i], inDefinitions)
			}
		}
	}
	view := raw
	for i := 0; i+1 < len(node.Content); i += 2 {
		key := node.Content[i].Value
		if child, present := view[key]; present {
			// expandRawRefs leaves definitions and test_vectors as written.
			walk(child, node.Content[i+1], key == "definitions" || key == "test_vectors")
		}
	}
	if id, ok := mapIdentity(raw); ok {
		var keys []string
		for i := 0; i+1 < len(node.Content); i += 2 {
			keys = append(keys, node.Content[i].Value)
		}
		order[id] = keys
	}
	return order
}

func derefNode(node *yaml.Node) *yaml.Node {
	for node != nil {
		switch node.Kind {
		case yaml.DocumentNode:
			if len(node.Content) == 0 {
				return nil
			}
			node = node.Content[0]
		case yaml.AliasNode:
			node = node.Alias
		default:
			return node
		}
	}
	return nil
}

func mappingValue(node *yaml.Node, key string) *yaml.Node {
	if node == nil || node.Kind != yaml.MappingNode {
		return nil
	}
	for i := 0; i+1 < len(node.Content); i += 2 {
		if node.Content[i].Value == key {
			return derefNode(node.Content[i+1])
		}
	}
	return nil
}
