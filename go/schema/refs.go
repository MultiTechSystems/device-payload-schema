// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"strings"
)

// CR-2026-045: definitions and references.
//
// Every `{$ref: '#/definitions/<name>'}` is spliced into the field list it sits in before
// the schema is parsed, so a reference works in any field list - a port entry, an
// object, a repeat, a case (PS-346, PS-347) - and the typed parser never sees one. The
// same pass rejects what the specification says must be rejected at load:
//
//   - a definition that is not a field group with a `fields` array (PS-345);
//   - a reference that does not resolve (PS-348), or names another document - support
//     for which is optional, and this implementation does not have it (PS-462);
//   - a pointer not of the form #/definitions/<name> (PS-461);
//   - a cycle, direct or transitive (PS-349).
//
// It mirrors expand_refs in tools/schema_interpreter.py.

const refPrefix = "#/definitions/"

func expandRawRefs(raw map[string]any) (map[string]any, error) {
	definitions := asStringMap(raw["definitions"])
	if raw["definitions"] != nil && definitions == nil {
		return nil, fmt.Errorf("'definitions' must map names to field groups (PS-345)")
	}
	for name, group := range definitions {
		if _, ok := asStringMap(group)["fields"].([]any); !ok {
			return nil, fmt.Errorf("definition '%s' is not a field group with a `fields` array (PS-345)", name)
		}
	}

	var expand func(node any, stack []string) (any, error)
	resolve := func(ref any, stack []string) ([]any, error) {
		text, isString := ref.(string)
		if !isString || !strings.HasPrefix(text, refPrefix) {
			if isString && strings.Contains(text, "#") && !strings.HasPrefix(text, "#") {
				return nil, fmt.Errorf("$ref %q names another document; this implementation resolves only #/definitions/<name> (PS-462)", text)
			}
			return nil, fmt.Errorf("$ref %v is not of the form #/definitions/<name> (PS-461)", ref)
		}
		name := strings.TrimPrefix(text, refPrefix)
		for _, seen := range stack {
			if seen == name {
				return nil, fmt.Errorf("$ref cycle: %s -> %s (PS-349)", strings.Join(stack, " -> "), name)
			}
		}
		fields, ok := asStringMap(definitions[name])["fields"].([]any)
		if !ok {
			return nil, fmt.Errorf("$ref %q does not resolve to a field group (PS-348)", text)
		}
		expanded, err := expand(fields, append(append([]string{}, stack...), name))
		if err != nil {
			return nil, err
		}
		return expanded.([]any), nil
	}
	expand = func(node any, stack []string) (any, error) {
		switch typed := node.(type) {
		case []any:
			out := make([]any, 0, len(typed))
			for _, item := range typed {
				if m := asStringMap(item); m != nil {
					if ref, ok := m["$ref"]; ok {
						spliced, err := resolve(ref, stack)
						if err != nil {
							return nil, err
						}
						out = append(out, spliced...)
						continue
					}
				}
				expanded, err := expand(item, stack)
				if err != nil {
					return nil, err
				}
				out = append(out, expanded)
			}
			return out, nil
		case map[string]any, map[any]any:
			m := asStringMap(typed)
			out := make(map[string]any, len(m))
			for key, value := range m {
				expanded, err := expand(value, stack)
				if err != nil {
					return nil, err
				}
				out[key] = expanded
			}
			return out, nil
		}
		return node, nil
	}

	out := make(map[string]any, len(raw))
	for key, value := range raw {
		if key == "definitions" || key == "test_vectors" {
			out[key] = value
			continue
		}
		expanded, err := expand(value, nil)
		if err != nil {
			return nil, err
		}
		out[key] = expanded
	}
	// A definition no field list reaches is still checked for cycles and dangling refs.
	for name, group := range definitions {
		if _, err := expand(asStringMap(group)["fields"], []string{name}); err != nil {
			return nil, err
		}
	}
	return out, nil
}
