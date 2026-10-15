// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"math"
	"regexp"
)

// CR-2026-176 (PS-361 amended, PS-590 to PS-592): a `type: string` field declaring
// `template` joins values decoded earlier into text and reads no bytes. `${name}` writes
// what a name_from template writes (PS-532): the label of a looked-up field, a number as
// formatLookupValue writes it, a string as it is, a boolean as true or false.
// `${name:format}` writes the number the reference binds (PS-523) in a part format of
// PS-430, through CR-2026-170's formatPartNumber; `$$` writes `$`. A reference with no
// value makes the field absent (PS-592); a format given a value that is not an integer, or
// hex given a negative one, makes it absent with a warning (PS-591). Mirrors
// render_string_template and string_template_errors in tools/schema_interpreter.py; the
// texts are the spec prototype's.

// stringTemplatePattern matches `$$`, `${name}` and `${name:format}`.
var stringTemplatePattern = regexp.MustCompile(`\$\$|\$\{(\w+)(?::([^}]*))?\}`)

// stringTemplateReference is the names a template references (PS-539).
var stringTemplateReference = regexp.MustCompile(`\$\{(\w+)(?::[^}]*)?\}`)

// isStringTemplate reports a `type: string` field declaring `template`: computed, with no
// bytes of its own.
func isStringTemplate(f Field) bool {
	return f.HasTemplate && (f.Type == TypeString || f.Type == TypeStringLower)
}

// stringNeitherMessage is PS-361 for a string field declaring neither key.
func stringNeitherMessage(name string) string {
	return fmt.Sprintf("Field '%s': `type: string` is a literal that declares `value`, or a "+
		"template that declares `template`; a string read from the payload is `ascii` "+
		"(PS-361)", name)
}

// templateGivenText writes a value in a PS-591 warning as the spec prototype's js_text
// does: a boolean as true or false, a number as JavaScript's String(n), else quoted.
func templateGivenText(v any) string {
	switch x := v.(type) {
	case bool:
		if x {
			return "true"
		}
		return "false"
	case string:
		return "'" + x + "'"
	}
	if f, ok := toFloat64(v); ok {
		return givenText(f)
	}
	return fmt.Sprintf("'%v'", v)
}

// templateInteger is a bound value as a sign and magnitude, where it is an integer.
func templateInteger(v any) (bool, uint64, bool) {
	switch x := v.(type) {
	case bool, string, nil:
		return false, 0, false
	case uint64:
		return false, x, true
	case uint:
		return false, uint64(x), true
	case uint8:
		return false, uint64(x), true
	case uint16:
		return false, uint64(x), true
	case uint32:
		return false, uint64(x), true
	case int64:
		if x < 0 {
			return true, uint64(-(x + 1)) + 1, true
		}
		return false, uint64(x), true
	case int:
		return templateInteger(int64(x))
	case int8:
		return templateInteger(int64(x))
	case int16:
		return templateInteger(int64(x))
	case int32:
		return templateInteger(int64(x))
	}
	f, ok := toFloat64(v)
	if !ok || math.IsNaN(f) || math.IsInf(f, 0) || f != math.Trunc(f) || math.Abs(f) >= 1.8446744073709552e19 {
		return false, 0, false
	}
	if f < 0 {
		return true, uint64(-f), true
	}
	return false, uint64(f), true
}

// renderStringTemplate is a template string field's value, or `omitted` (PS-590 to
// PS-592).
func (ctx *DecodeContext) renderStringTemplate(field Field) any {
	absent := false
	text := stringTemplatePattern.ReplaceAllStringFunc(field.Template, func(token string) string {
		if absent {
			return ""
		}
		m := stringTemplatePattern.FindStringSubmatch(token)
		reference := m[1]
		if reference == "" {
			return "$"
		}
		hasFormat := len(token) > len(reference)+3 // `${` + name + `}` is len+3
		number, bound := ctx.Variables[reference]
		if !bound || number == omitted || number == absentSentinel {
			absent = true
			return ""
		}
		if !hasFormat {
			value := number
			if entry, has := ctx.labels[reference]; has && sameValue(number, entry.bound) {
				value = entry.reported
			}
			if value == omitted {
				absent = true
				return ""
			}
			out, err := renderTemplateValue(value, reference, field.Template)
			if err != nil {
				absent = true
				return ""
			}
			return out
		}
		format := m[2]
		base, width, err := parsePartFormat(format)
		neg, magnitude, integral := templateInteger(number)
		if err != nil || !integral || (neg && (base == "hex" || base == "hex:upper")) {
			ctx.Warnings = append(ctx.Warnings, fmt.Sprintf("Field '%s': ${%s:%s} is given %s, "+
				"which the format cannot write, so the field is absent (PS-591)",
				field.Name, reference, format, templateGivenText(number)))
			absent = true
			return ""
		}
		return formatPartNumber(neg, magnitude, base, width)
	})
	if absent {
		return omitted
	}
	return text
}

// checkStringTemplate is PS-361 and PS-590 over one field mapping at load, in the spec
// prototype's text: `template` is a string on a `type: string` field without `value`, a
// string field declares one of the two, and every ${name:format} is a part format (PS-430).
func checkStringTemplate(field map[string]any) error {
	template, has := field["template"]
	typ, _ := field["type"].(string)
	_, hasValue := field["value"]
	name, _ := field["name"].(string)
	if !has {
		if typ == "string" && !hasValue {
			return fmt.Errorf("%s", stringNeitherMessage(name))
		}
		return nil
	}
	text, isText := template.(string)
	if typ != "string" || hasValue || !isText {
		if name == "" {
			name = "<unnamed>"
		}
		return fmt.Errorf("The template of '%s' is a string on a `type: string` field that "+
			"declares no `value` (PS-361, PS-590)", name)
	}
	for _, m := range stringTemplatePattern.FindAllStringSubmatch(text, -1) {
		if m[1] != "" && len(m[0]) > len(m[1])+3 {
			if _, _, err := parsePartFormat(m[2]); err != nil {
				return err
			}
		}
	}
	return nil
}

// checkTemplateAggregates is PS-532 for a template string: it references no object or
// repeat its field list declared before it.
func checkTemplateAggregates(field map[string]any, aggregates map[string]bool) error {
	text, ok := field["template"].(string)
	if !ok {
		return nil
	}
	for _, m := range stringTemplateReference.FindAllStringSubmatch(text, -1) {
		if aggregates[m[1]] {
			name, _ := field["name"].(string)
			if name == "" {
				name = "<unnamed>"
			}
			return fmt.Errorf("The template of '%s' references `%s`, an object or repeat, "+
				"which has no text (PS-532, PS-590)", name, m[1])
		}
	}
	return nil
}
