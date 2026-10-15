package schema

import (
	"fmt"
	"math"
	"strings"
)

// CR-2026-134 (PS-551): an encoder rejects input that omits a value the schema requires,
// naming the field, and derives a value the rest of the input fixes.
//
// The helpers mirror tools/schema_interpreter.py function for function:
// missing_field_message, _size_source, _derivable, _is_peek, _derived_source,
// _derive_inputs, _sole_case_given, _flagged_group_given and _repeat_size.

// missingFieldError is what every encoder says for a required value the input omits.
// The spec prototype's text, so a cross-language diff compares equal strings.
func missingFieldError(name string) error {
	return fmt.Errorf("Missing required field: %s", name)
}

// fieldDefault is a field's declared `default` where the input omits it (PS-554,
// CR-2026-139): in the field's reported form, encoded exactly as that value given as
// input, so one that cannot be encoded is rejected as such input is (PS-168). A declared
// default is a defined absence (PS-551 as amended). An enum's `default` is its unmapped
// label (PS-068), so it is not written.
// Read from the declaration as written: the typed model keeps only the enum's.
func fieldDefault(f Field) (any, bool) {
	if f.decl == nil {
		return nil, false
	}
	v, ok := f.decl["default"]
	if !ok {
		return nil, false
	}
	if t, _ := f.decl["type"].(string); t == "enum" {
		return nil, false
	}
	return v, true
}

// isComputedField reports a field computed from others, which has no bytes of its own.
func isComputedField(f Field) bool {
	return f.Type == TypeNumber || f.Type == "number" || f.Type == TypeInteger ||
		f.Type == "integer" || f.IntegerResult
}

// isLiteralField reports a `string`, `number` or `boolean` field declaring `value`
// (PS-357): a constant read from no bytes.
func isLiteralField(f Field) bool {
	return f.Value != nil && (f.Type == TypeString || f.Type == TypeStringLower ||
		f.Type == TypeNumber || f.Type == "number" || f.Type == TypeBoolean)
}

// sizeSource is the field of this list a `$ref` names, by its name or its `var:`,
// searched into a byte_group's members, since a count is often a few bits of a shared
// byte. Not into any other construct.
func sizeSource(fields []Field, ref string) (Field, bool) {
	for _, f := range fields {
		if len(f.ByteGroup) > 0 {
			if found, ok := sizeSource(f.ByteGroup, ref); ok {
				return found, true
			}
			continue
		}
		if f.Name != "" && (f.Name == ref || f.Var == ref) {
			return f, true
		}
	}
	return Field{}, false
}

// derivable reports whether a value can be derived into a field and written as it
// stands: it reads bytes of its own and reports them unchanged.
func derivable(f Field) bool {
	if f.Name == "" || isComputedField(f) || f.Value != nil || f.NameFrom != "" {
		return false
	}
	if len(f.Lookup) > 0 || len(f.LookupArray) > 0 || f.LookupDefault != nil {
		return false
	}
	if f.Mult != nil || f.Div != nil || f.Add != nil || len(f.Transform) > 0 ||
		len(f.Modifiers) > 0 || len(f.Polynomial) > 0 || f.Encoding != "" ||
		f.Compute != nil || f.Ref != "" || f.Guard != nil {
		return false
	}
	return true
}

// isPeek reports a bit range in a run that nothing closes with `consume`. Such a run
// reads bytes the fields after it read again, and the encoder writes them twice, so a
// value derived into it would add bytes rather than fix them.
func isPeek(fields []Field, source Field) bool {
	for i := 0; i < len(fields); i++ {
		if !isBareBitfieldField(fields[i]) {
			continue
		}
		start := i
		for fields[i].Consume < 1 && i+1 < len(fields) && isBareBitfieldField(fields[i+1]) {
			i++
		}
		for j := start; j <= i; j++ {
			if fields[j].Name == source.Name && fields[j].Type == source.Type {
				return fields[i].Consume < 1
			}
		}
	}
	return false
}

// derivedSource is the field a `$ref` names, where a value can be derived into it.
func derivedSource(fields []Field, ref any) (Field, bool) {
	text, ok := ref.(string)
	if !ok || !strings.HasPrefix(text, "$") {
		return Field{}, false
	}
	source, ok := sizeSource(fields, text[1:])
	if !ok || !derivable(source) || isPeek(fields, source) {
		return Field{}, false
	}
	return source, true
}

// repeatRecords is a repeat's list as the input gives it; ok is false where it gives none.
func repeatRecords(field Field, data map[string]any) ([]any, bool) {
	switch v := data[field.Name].(type) {
	case []any:
		return v, true
	case map[string]any:
		return []any{v}, true
	}
	return nil, false
}

// exactCaseValue is the one discriminator a match case stands for; false for a list,
// a range, `default` or a pattern.
func exactCaseValue(c Case) (int64, bool) {
	if c.Default {
		return 0, false
	}
	raw := c.Case
	switch v := raw.(type) {
	case bool:
		return 0, false
	case int:
		return int64(v), true
	case int64:
		return v, true
	case uint64:
		return int64(v), true
	case float64:
		if v == math.Trunc(v) {
			return int64(v), true
		}
		return 0, false
	case string:
		n, err := parseIntAny(v)
		return n, err == nil
	}
	return 0, false
}

// soleCaseGiven is the discriminator of the one match case whose fields the input
// gives; false where none is, two explain it equally well, the default explains it as
// well, or the case stands for more than one value. Scored by bodyScore, so the fields
// of a match nested in a case count, and a literal the input contradicts rules it out.
func soleCaseGiven(m Field, data map[string]any) (int64, bool) {
	best, tied, ok := bestCase(m, data, nil)
	if !ok || tied {
		return 0, false
	}
	return exactCaseValue(best)
}

// flaggedGroupGiven reports whether the input gives any of a flagged group's values,
// its own or those of a construct inside it.
func flaggedGroupGiven(group FlaggedGroup, data map[string]any) bool {
	for _, gf := range group.Fields {
		if gf.Name != "" {
			if _, ok := data[gf.Name]; ok {
				return true
			}
		}
	}
	for _, f := range claimableFields(group.Fields, nil) {
		if _, ok := data[f.Name]; ok {
			return true
		}
	}
	return false
}

// deriveInputs is PS-551's derivation for one field list: a repeat's `count: $n` or
// `byte_length: $n` field from the list given, a match's `field: $d` discriminator from
// the one case whose fields are given, and a flagged construct's mask from the groups
// given. A supplied value is kept; the construct checks it agrees. A flagged mask that
// disagrees with the groups given is rejected here. The input is not modified.
func deriveInputs(fields []Field, data map[string]any, ctx *EncodeContext) (map[string]any, error) {
	derived, copied := data, false
	set := func(name string, value any) {
		if !copied {
			derived = make(map[string]any, len(data)+1)
			for k, v := range data {
				derived[k] = v
			}
			copied = true
		}
		derived[name] = value
	}
	absent := func(name string) bool {
		v, ok := derived[name]
		return !ok || v == nil
	}
	if ctx.trace != nil {
		// PS-573: what the decode record supplies, before anything is derived.
		set("", nil)
		delete(derived, "")
		if err := recordedInputs(fields, data, derived, ctx.trace); err != nil {
			return nil, err
		}
	}
	for _, f := range fields {
		switch {
		case isByteStringType(f.Type) && f.LengthRef != "" && data[f.Name] != nil:
			// PS-602: a byte string's value fixes the field its length names (CR-2026-181).
			if err := deriveByteCount(fields, f, data, absent, set, derived); err != nil {
				return nil, err
			}
		case f.Type == TypeRepeat || f.Type == TypeRepeatLower:
			records, ok := repeatRecords(f, data)
			if !ok {
				continue
			}
			if source, ok := derivedSource(fields, f.Count); ok && absent(source.Name) {
				set(source.Name, float64(len(records)))
			}
			if source, ok := derivedSource(fields, f.ByteLength); ok && absent(source.Name) {
				scratch := ctx.branch()
				if err := encodeRepeatRecords(f, records, scratch, -1); err != nil {
					return nil, err
				}
				set(source.Name, float64(len(scratch.Buffer)))
			}
		case f.MatchInline != nil:
			source, ok := derivedSource(fields, f.MatchInline.On)
			if !ok || !absent(source.Name) {
				continue
			}
			if key, ok := soleCaseGiven(*f.MatchInline, data); ok {
				set(source.Name, float64(key))
			}
		case f.Flagged != nil:
			mask := strings.TrimPrefix(f.Flagged.Field, "$")
			name := mask
			if source, ok := sizeSource(fields, mask); ok {
				name = source.Name
			}
			bits, declared := 0, 0
			for _, group := range f.Flagged.Groups {
				declared |= 1 << uint(group.Bit)
				if flaggedGroupGiven(group, data) {
					bits |= 1 << uint(group.Bit)
				}
			}
			if _, recorded := recordedFlags(f.Flagged, ctx); recorded && data[name] == nil {
				// PS-573: the record's flags; the groups written are the ones they set
				// (encodeFlagged), so there is nothing to check.
				if absent(name) {
					flags, _ := recordedFlags(f.Flagged, ctx)
					set(name, float64(flags))
				}
				continue
			}
			given, present := derived[name]
			if !present || given == nil {
				set(name, float64(bits))
				continue
			}
			// The groups test what a reference to the mask binds (PS-523, CR-2026-132):
			// an enum's code, the number under a text label. Compared as reported, an
			// enum name or a text label passed unchecked.
			given = encodeDiscriminator(mask, given, fields)
			if _, isBool := given.(bool); isBool {
				continue
			}
			if num, ok := toFloat64(given); ok && num == math.Trunc(num) {
				if int(num)&declared != bits {
					return nil, fmt.Errorf("Flagged field '%s' is given %d, and the groups given set %d (PS-551)",
						name, int(num), bits)
				}
			}
		}
	}
	return derived, nil
}

// repeatSize is a repeat's `count` or `byte_length` as the schema or the input states
// it; false where neither states a whole number.
func repeatSize(raw any, data map[string]any, siblings []Field) (int, bool) {
	if text, ok := raw.(string); ok && strings.HasPrefix(text, "$") {
		name := text[1:]
		if source, ok := sizeSource(siblings, name); ok {
			name = source.Name
		}
		raw = data[name]
	}
	if _, isBool := raw.(bool); isBool {
		return 0, false
	}
	num, ok := toFloat64(raw)
	if !ok || num != math.Trunc(num) {
		return 0, false
	}
	return int(num), true
}
