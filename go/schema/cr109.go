package schema

import (
	"fmt"
	"reflect"
	"strconv"
	"strings"
)

// CR-2026-109 (PS-523, PS-524): what a `$` reference and a `var` bind for a field with a
// lookup, kept apart from what the field reports.
//
// A reference binds the number before the lookup where the lookup reports a string or a
// boolean, or has no entry for the value; it binds the label itself where the lookup
// reports a number, so a table of sizes still feeds arithmetic. A `name_from` template is
// not a reference: it writes the value reported, the label (PS-532). So the variable table
// holds the binding, and labels holds the reported value beside it for templates.

// lookupNumber is the number a looked-up field held just before its lookup, recorded by
// applyLookupAndModifiers for the field it was called with.
type lookupNumber struct {
	owner  string
	number any
	set    bool
}

// labelEntry is what a looked-up name reported (omitted on a miss), and the value bound
// for it then: a template reads the label only while the name is still bound to that.
type labelEntry struct {
	bound    any
	reported any
}

func hasLookup(field Field) bool {
	return field.Lookup != nil || field.LookupArray != nil
}

// isEnumField reports a `type: enum`, which PS-523 treats as a lookup by another spelling
// (CR-2026-132): a reference binds its code, a template reads its name.
func isEnumField(field Field) bool {
	return field.Type == TypeEnum || field.Type == TypeEnumLower
}

// reverseEnumLabel is the code an enum reported `value` for (CR-2026-132), including the
// "unknown(N)" marker; false where none is found.
func reverseEnumLabel(field Field, value any) (int, bool) {
	text, ok := value.(string)
	if !ok {
		return 0, false
	}
	for k, v := range field.Values {
		if v == text {
			return k, true
		}
	}
	if strings.HasPrefix(text, "unknown(") && strings.HasSuffix(text, ")") {
		if n, err := strconv.Atoi(text[8 : len(text)-1]); err == nil {
			return n, true
		}
	}
	return 0, false
}

// sameValue compares two bound values without panicking on an uncomparable type.
func sameValue(a, b any) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	ta, tb := reflect.TypeOf(a), reflect.TypeOf(b)
	if ta != tb || !ta.Comparable() {
		return false
	}
	return a == b
}

// bindReference binds `name` for a field as PS-523 says. `reported` is the value the
// field reports, or `omitted` where its lookup had no entry; a miss binds the number
// where one was recorded for this field, and binds nothing otherwise.
func (ctx *DecodeContext) bindReference(name string, field Field, reported any) {
	if name == "" {
		return
	}
	looked := (hasLookup(field) || isEnumField(field) || hasBitLabels(field)) && ctx.lookupNumber.set &&
		ctx.lookupNumber.owner == field.Name
	if !looked {
		if reported == omitted {
			return
		}
		ctx.Variables[name] = reported
		delete(ctx.labels, name)
		return
	}
	bound := reported
	if _, numeric := toFloat64(reported); !numeric {
		bound = ctx.lookupNumber.number
	}
	ctx.Variables[name] = bound
	if ctx.labels == nil {
		ctx.labels = map[string]labelEntry{}
	}
	ctx.labels[name] = labelEntry{bound: bound, reported: reported}
}

// rebindReported binds a key a nested construct reported, unless the construct's own
// decode already bound it as a looked-up field: copying the reported label over that
// binding would undo PS-523.
func (ctx *DecodeContext) rebindReported(key string, value any) {
	if entry, ok := ctx.labels[key]; ok && sameValue(ctx.Variables[key], entry.bound) &&
		sameValue(value, entry.reported) {
		return
	}
	ctx.Variables[key] = value
	delete(ctx.labels, key)
}

// templateValue is the value a `name_from` template writes for a reference: the label
// where the name is bound by a looked-up field (PS-532), else the bound value. A lookup
// that had no entry reported nothing for a template to write (PS-266).
func (ctx *DecodeContext) templateValue(field Field, reference string) (any, bool, error) {
	value, ok := ctx.Variables[reference]
	if !ok {
		return nil, false, nil
	}
	if entry, has := ctx.labels[reference]; has && sameValue(value, entry.bound) {
		if entry.reported == omitted {
			return nil, true, fmt.Errorf("name_from for '%s' references %s, which reported "+
				"no value (PS-266, PS-532)", field.Name, reference)
		}
		return entry.reported, true, nil
	}
	return value, true, nil
}

// dataThroughVar is the data's value for a variable name through the sibling field that
// declares it with `var`, whose name is often not the variable's (PS-267).
func dataThroughVar(varName string, siblings []Field, data map[string]any) (any, bool) {
	for _, sibling := range siblings {
		if sibling.Var == varName && sibling.Name != "" {
			v, ok := data[sibling.Name]
			return v, ok
		}
	}
	return nil, false
}

// encodeDiscriminator turns a match's `field` reference, read from the data as the label
// the field reported, back into the number it binds (PS-523): a text or boolean label is
// reversed through the declaring field's lookup, a numeric label is the binding itself.
func encodeDiscriminator(varName string, value any, siblings []Field) any {
	if _, numeric := toFloat64(value); numeric {
		return value
	}
	for _, sibling := range siblings {
		if (sibling.Name == varName || sibling.Var == varName) && hasLookup(sibling) {
			if number, err := reverseLookupLabel(sibling, value); err == nil {
				return number
			}
			return value
		}
		if (sibling.Name == varName || sibling.Var == varName) && isEnumField(sibling) {
			if code, ok := reverseEnumLabel(sibling, value); ok {
				return code
			}
			return value
		}
	}
	return value
}
