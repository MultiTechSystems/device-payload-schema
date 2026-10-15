// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"strings"

	"gopkg.in/yaml.v3"
)

// CR-2026-045 and CR-2026-168: definitions and references.
//
// A definition carries exactly one of `fields` (a field group), `field` (one field) or
// `lookup` (a table) (PS-345). Every `{$ref: '#/definitions/<name>'}` in a field list is
// replaced by the group's fields, or by the one field with each key the reference carries
// replacing the defined key whole (PS-346, PS-578), and a field's `lookup` given as
// `#/definitions/<name>` becomes that table (PS-579). References resolve transitively
// (PS-580). The definitions kept on the document are themselves resolved, so the typed
// parser and every later check never see a reference.
//
// Resolution works on the YAML node tree, before any value is constructed, so a resolved
// field keeps its source key order - which `_meta`, PS-539's walk and a match's case order
// (PS-563) read through keyOrder. It rejects, in the spec prototype's words:
//
//   - a definition carrying none, or several, of `fields`, `field` and `lookup`, or one of
//     the wrong shape (PS-345);
//   - a reference that does not resolve, or names a definition of the wrong kind (PS-348),
//     or names another document - support for which is optional, and this implementation
//     does not have it (PS-462);
//   - a group reference carrying other keys, or a resolved field with no name (PS-578);
//   - a pointer not of the form #/definitions/<name> (PS-461);
//   - a cycle, direct or transitive (PS-349).
//
// It mirrors expand_refs in tools/schema_interpreter.py.

const refPrefix = "#/definitions/"

// definitionKinds are the three kinds of definition, in the order a message lists them.
var definitionKinds = []string{"fields", "field", "lookup"}

var definitionShapes = map[string]string{"fields": "a list of fields", "field": "a field",
	"lookup": "a lookup table"}

// resolveRefNodes returns the document node with every reference resolved. Errors are
// found in the order the reference finds them: the document's keys first, then every
// definition, used or not.
func resolveRefNodes(root *yaml.Node) (*yaml.Node, error) {
	doc := derefNode(root)
	if doc == nil || doc.Kind != yaml.MappingNode {
		return root, nil
	}
	r := &refResolver{definitions: map[string]*yaml.Node{}}
	var definitionsNode *yaml.Node
	for i := 0; i+1 < len(doc.Content); i += 2 {
		if doc.Content[i].Value != "definitions" {
			continue
		}
		definitionsNode = derefNode(doc.Content[i+1])
		if definitionsNode != nil && definitionsNode.ShortTag() == "!!null" {
			definitionsNode = nil
		}
		if definitionsNode != nil && definitionsNode.Kind != yaml.MappingNode {
			return nil, fmt.Errorf("`definitions` must map names to definitions (PS-345)")
		}
	}
	if definitionsNode != nil {
		for j := 0; j+1 < len(definitionsNode.Content); j += 2 {
			r.definitions[definitionsNode.Content[j].Value] = derefNode(definitionsNode.Content[j+1])
		}
	}

	resolved := make([]*yaml.Node, len(doc.Content))
	for i := 0; i+1 < len(doc.Content); i += 2 {
		key, value := doc.Content[i], doc.Content[i+1]
		resolved[i] = key
		if key.Value == "definitions" || key.Value == "test_vectors" {
			resolved[i+1] = value
			continue
		}
		expanded, err := r.expand(value, nil)
		if err != nil {
			return nil, err
		}
		resolved[i+1] = expanded
	}
	if definitionsNode != nil {
		definitions := &yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
		for j := 0; j+1 < len(definitionsNode.Content); j += 2 {
			name := definitionsNode.Content[j].Value
			kind, err := r.kind(name, nil)
			if err != nil {
				return nil, err
			}
			group := r.definitions[name]
			body := cloneNode(mappingValue(group, kind))
			if kind != "lookup" {
				if body, err = r.expand(mappingValue(group, kind), []string{name}); err != nil {
					return nil, err
				}
			}
			entry := &yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
			for k := 0; k+1 < len(group.Content); k += 2 {
				value := cloneNode(group.Content[k+1])
				if group.Content[k].Value == kind {
					value = body
				}
				entry.Content = append(entry.Content, group.Content[k], value)
			}
			definitions.Content = append(definitions.Content, definitionsNode.Content[j], entry)
		}
		for i := 0; i+1 < len(doc.Content); i += 2 {
			if doc.Content[i].Value == "definitions" {
				resolved[i+1] = definitions
			}
		}
	}
	out := *doc
	out.Content = resolved
	return &out, nil
}

type refResolver struct {
	definitions map[string]*yaml.Node
}

// kind is the kind of definition `name` is (PS-345), checked for a cycle (PS-349) and for
// being defined at all (PS-348).
func (r *refResolver) kind(name string, chain []string) (string, error) {
	for _, seen := range chain {
		if seen == name {
			return "", fmt.Errorf("`$ref` cycle: %s (PS-349)",
				strings.Join(append(append([]string{}, chain...), name), " -> "))
		}
	}
	group, defined := r.definitions[name]
	if !defined {
		return "", fmt.Errorf("`$ref` to '%s%s', which `definitions` does not define (PS-348)", refPrefix, name)
	}
	var kinds []string
	for _, kind := range definitionKinds {
		if group != nil && group.Kind == yaml.MappingNode && hasKey(group, kind) {
			kinds = append(kinds, kind)
		}
	}
	if len(kinds) != 1 {
		listed := "none"
		if len(kinds) > 0 {
			quoted := make([]string, len(kinds))
			for i, kind := range kinds {
				quoted[i] = "'" + kind + "'"
			}
			listed = "[" + strings.Join(quoted, ", ") + "]"
		}
		return "", fmt.Errorf("definition '%s' must carry exactly one of `fields`, `field` or `lookup`, not %s (PS-345)", name, listed)
	}
	body := mappingValue(group, kinds[0])
	var ok bool
	switch kinds[0] {
	case "fields":
		ok = body != nil && body.Kind == yaml.SequenceNode
	case "field":
		ok = body != nil && body.Kind == yaml.MappingNode
	default:
		ok = body != nil && (body.Kind == yaml.MappingNode || body.Kind == yaml.SequenceNode)
	}
	if !ok {
		return "", fmt.Errorf("definition '%s': `%s` must be %s (PS-345)", name, kinds[0], definitionShapes[kinds[0]])
	}
	return kinds[0], nil
}

func hasKey(node *yaml.Node, key string) bool {
	for i := 0; i+1 < len(node.Content); i += 2 {
		if node.Content[i].Value == key {
			return true
		}
	}
	return false
}

func isStringNode(node *yaml.Node) bool {
	return node != nil && node.Kind == yaml.ScalarNode && node.ShortTag() == "!!str"
}

// target is the definition name a pointer names (PS-461, PS-462).
func (r *refResolver) target(ref *yaml.Node) (string, error) {
	ref = derefNode(ref)
	if isStringNode(ref) {
		name := strings.TrimPrefix(ref.Value, refPrefix)
		if strings.HasPrefix(ref.Value, refPrefix) && name != "" && !strings.Contains(name, "/") {
			return name, nil
		}
		if strings.Contains(ref.Value, "#") && !strings.HasPrefix(ref.Value, "#") {
			return "", fmt.Errorf("$ref %s names another document; this implementation resolves only #/definitions/<name> (PS-462)", sampledRepr(ref.Value))
		}
	}
	return "", fmt.Errorf("$ref %s is not of the form #/definitions/<name> (PS-461)", sampledRepr(nodeValue(ref)))
}

func nodeValue(node *yaml.Node) any {
	var value any
	if node != nil {
		_ = node.Decode(&value)
	}
	return value
}

// reference resolves one `$ref` entry of a field list into the fields it stands for.
func (r *refResolver) reference(item *yaml.Node, chain []string) ([]*yaml.Node, error) {
	name, err := r.target(mappingValue(item, "$ref"))
	if err != nil {
		return nil, err
	}
	kind, err := r.kind(name, chain)
	if err != nil {
		return nil, err
	}
	var overrides []*yaml.Node
	for i := 0; i+1 < len(item.Content); i += 2 {
		if item.Content[i].Value != "$ref" {
			overrides = append(overrides, item.Content[i], item.Content[i+1])
		}
	}
	inner := append(append([]string{}, chain...), name)
	switch kind {
	case "lookup":
		return nil, fmt.Errorf("`$ref` to '%s%s' in a field list names a lookup table, not a field or a field group (PS-346, PS-348)", refPrefix, name)
	case "fields":
		if len(overrides) > 0 {
			return nil, fmt.Errorf("a `$ref` to the field group '%s' carries nothing else: %s (PS-578)", name, sampledRepr(nodeValue(item)))
		}
		expanded, err := r.expand(mappingValue(r.definitions[name], "fields"), inner)
		if err != nil {
			return nil, err
		}
		return expanded.Content, nil
	}
	field, err := r.expand(mappingValue(r.definitions[name], "field"), inner)
	if err != nil {
		return nil, err
	}
	// Each key the reference carries replaces the defined key whole (PS-578); a key the
	// definition lacks is added after its own.
	for i := 0; i+1 < len(overrides); i += 2 {
		var value *yaml.Node
		if overrides[i].Value == "lookup" {
			value, err = r.expandLookup(overrides[i+1], chain)
		} else {
			value, err = r.expand(overrides[i+1], chain)
		}
		if err != nil {
			return nil, err
		}
		replaced := false
		for k := 0; k+1 < len(field.Content); k += 2 {
			if field.Content[k].Value == overrides[i].Value {
				field.Content[k+1] = value
				replaced = true
			}
		}
		if !replaced {
			field.Content = append(field.Content, cloneNode(overrides[i]), value)
		}
	}
	typeNode := mappingValue(field, "type")
	if !isStringNode(mappingValue(field, "name")) && !(typeNode != nil && typeNode.Value == "skip") {
		return nil, fmt.Errorf("the field resolved from '%s%s' has no `name`; the definition or the reference declares one (PS-578, PS-553)", refPrefix, name)
	}
	return []*yaml.Node{field}, nil
}

// expandLookup is a field's `lookup`: a pointer becomes the table it names (PS-579); a
// table is copied as it is, since its keys are data.
func (r *refResolver) expandLookup(value *yaml.Node, chain []string) (*yaml.Node, error) {
	value = derefNode(value)
	if !isStringNode(value) {
		return cloneNode(value), nil
	}
	name, err := r.target(value)
	if err != nil {
		return nil, err
	}
	kind, err := r.kind(name, chain)
	if err != nil {
		return nil, err
	}
	if kind != "lookup" {
		return nil, fmt.Errorf("`lookup: %s` names a definition that is not a lookup table (PS-579, PS-348)", value.Value)
	}
	return cloneNode(mappingValue(r.definitions[name], "lookup")), nil
}

// expand returns a fresh copy of node with every reference in it resolved.
func (r *refResolver) expand(node *yaml.Node, chain []string) (*yaml.Node, error) {
	node = derefNode(node)
	if node == nil {
		return nil, nil
	}
	copied := *node
	copied.Anchor = ""
	copied.Content = nil
	switch node.Kind {
	case yaml.SequenceNode:
		for _, item := range node.Content {
			if m := derefNode(item); m != nil && m.Kind == yaml.MappingNode && hasKey(m, "$ref") {
				spliced, err := r.reference(m, chain)
				if err != nil {
					return nil, err
				}
				copied.Content = append(copied.Content, spliced...)
				continue
			}
			expanded, err := r.expand(item, chain)
			if err != nil {
				return nil, err
			}
			copied.Content = append(copied.Content, expanded)
		}
	case yaml.MappingNode:
		for i := 0; i+1 < len(node.Content); i += 2 {
			key := node.Content[i]
			var expanded *yaml.Node
			var err error
			if key.Value == "lookup" {
				expanded, err = r.expandLookup(node.Content[i+1], chain)
			} else {
				expanded, err = r.expand(node.Content[i+1], chain)
			}
			if err != nil {
				return nil, err
			}
			copied.Content = append(copied.Content, cloneNode(key), expanded)
		}
	}
	return &copied, nil
}

// cloneNode is a deep copy with every alias replaced by a copy of what it names.
func cloneNode(node *yaml.Node) *yaml.Node {
	node = derefNode(node)
	if node == nil {
		return nil
	}
	copied := *node
	copied.Anchor = ""
	copied.Content = make([]*yaml.Node, len(node.Content))
	for i, child := range node.Content {
		copied.Content[i] = cloneNode(child)
	}
	return &copied
}
