package schema

import (
	"strconv"
	"strings"
)

// CR-2026-151 (PS-551), applied on spec main for 0.5.5: a nested match that reads
// a discriminator fixes it, where the input fills exactly one of its cases, that case's key
// is a single value, and the value selects, in the enclosing match and in every match
// between that reads the same discriminator, the branch holding the nested match. Mirrors
// _nested_discriminator in tools/schema_interpreter.py.

// nestedStep is a match on the way down and the branch taken in it: an index into Cases,
// or -1 for the `default` beside them.
type nestedStep struct {
	match  Field
	branch int
}

// nestedDiscriminator is the value a nested match fixes for `field`'s discriminator, with
// the branch it selects; found is false where no nested match reading it has a case the
// input fills. Where one does but the input fixes no single value selecting the branch
// holding it, the input is rejected naming the discriminator.
func nestedDiscriminator(field Field, data map[string]any, label string) (int64, []Field, string, bool, error) {
	names := map[string]bool{}
	if field.Var != "" {
		names[field.Var] = true
	}
	if field.Name != "" {
		names[field.Name] = true
	}
	if len(names) == 0 {
		return 0, nil, "", false, nil
	}
	namesOf := func(fields []Field) []string {
		var out []string
		for _, f := range fields {
			if f.Name != "" && !strings.HasPrefix(f.Name, "_") && !isComputedField(f) {
				out = append(out, f.Name)
			}
		}
		return out
	}
	// A case is filled where the input gives every one of its fields and nothing any
	// other case of its match declares beyond them (Netvox r718n3's case 3 holds the
	// fields of cases 1 and 2 together).
	filledCase := func(own []string, given map[string]bool) bool {
		if len(own) == 0 {
			return false
		}
		mine := map[string]bool{}
		for _, n := range own {
			if _, ok := data[n]; !ok {
				return false
			}
			mine[n] = true
		}
		for n := range given {
			if !mine[n] {
				return false
			}
		}
		return true
	}
	type filledEntry struct {
		path []nestedStep
		c    Case
	}
	var filled []filledEntry
	var walk func(fields []Field, path []nestedStep)
	walk = func(fields []Field, path []nestedStep) {
		for _, f := range fields {
			if f.MatchInline == nil {
				continue
			}
			inner := *f.MatchInline
			reads := inner.On != "" && names[strings.TrimPrefix(inner.On, "$")]
			given := map[string]bool{}
			if reads {
				for _, c := range inner.Cases {
					if !c.Default {
						for _, n := range namesOf(c.Fields) {
							if _, ok := data[n]; ok {
								given[n] = true
							}
						}
					}
				}
			}
			for i, c := range inner.Cases {
				next := path
				if reads {
					if !c.Default && filledCase(namesOf(c.Fields), given) {
						filled = append(filled, filledEntry{path, c})
					}
					next = append(append([]nestedStep{}, path...), nestedStep{inner, i})
				}
				walk(c.Fields, next)
			}
			if body := matchDefaultFields(inner); body != nil {
				next := path
				if reads {
					next = append(append([]nestedStep{}, path...), nestedStep{inner, -1})
				}
				walk(body, next)
			}
		}
	}
	for i, c := range field.Cases {
		walk(c.Fields, []nestedStep{{field, i}})
	}
	if body := matchDefaultFields(field); body != nil {
		walk(body, []nestedStep{{field, -1}})
	}
	if len(filled) == 0 {
		return 0, nil, "", false, nil
	}
	if len(filled) != 1 {
		return 0, nil, "", false, missingFieldError(label)
	}
	raw := filled[0].c.Case
	if raw == nil {
		raw = filled[0].c.Match
	}
	value, single := exactCaseValue(filled[0].c)
	if !single {
		return 0, nil, "", false, missingFieldError(label)
	}
	for _, step := range filled[0].path {
		selected := -1
		for i, c := range step.match.Cases {
			if c.Default {
				if selected == -1 {
					selected = i
				}
				continue
			}
			if caseMatchesValue(int(value), c) {
				selected = i
				break
			}
		}
		// An in-cases `default` stands in for the default beside them.
		if selected >= 0 && step.match.Cases[selected].Default && step.branch == -1 {
			selected = -1
		}
		if selected != step.branch {
			return 0, nil, "", false, missingFieldError(label)
		}
	}
	outer := filled[0].path[0]
	if outer.branch == -1 {
		return value, matchDefaultFields(field), "default", true, nil
	}
	c := field.Cases[outer.branch]
	if c.Default {
		return value, c.Fields, "default", true, nil
	}
	key := raw
	return value, c.Fields, toKeyString(key), true, nil
}

// matchDefaultFields is the field list a match's `default:` beside its cases holds, or nil.
func matchDefaultFields(m Field) []Field {
	switch fallback := m.MatchDefault.(type) {
	case []any:
		return parseFieldsRaw(fallback)
	case []Field:
		return fallback
	}
	return nil
}

func toKeyString(raw any) string {
	if s, ok := raw.(string); ok {
		return s
	}
	if n, ok := exactCaseValue(Case{Case: raw}); ok {
		return strconv.FormatInt(n, 10)
	}
	return ""
}
