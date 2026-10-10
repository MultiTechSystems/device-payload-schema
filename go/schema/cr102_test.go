// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"strings"
	"testing"
)

// CR-2026-102: a field's ipso is a mapping carrying object, and its senml a mapping
// (PS-510), wherever the field sits. The same cases are in
// tests/test_cr_2026_102_ipso_senml_mappings.py, CR2026102Test and CR2026_102Tests.

const (
	cr102IpsoMessage  = "The ipso of 't' is a mapping that carries `object`, such as {object: 3303}; a bare object number is not a shorthand (PS-510)"
	cr102SenmlMessage = "The senml of 't' is a mapping, such as {unit: Cel} (PS-510)"
)

var cr102Contexts = map[string]string{
	"top":        "fields:\n  - FIELD\n",
	"port":       "ports:\n  1:\n    fields:\n      - FIELD\n",
	"match":      "fields:\n  - {name: kind, type: u8}\n  - match:\n      field: $kind\n      cases:\n        1:\n          - FIELD\n",
	"tlv":        "fields:\n  - tlv:\n      tag_size: 1\n      cases:\n        1:\n          - FIELD\n",
	"object":     "fields:\n  - name: o\n    type: object\n    fields:\n      - FIELD\n",
	"repeat":     "fields:\n  - name: r\n    type: repeat\n    count: 2\n    fields:\n      - FIELD\n",
	"byte_group": "fields:\n  - byte_group:\n      size: 1\n      fields:\n        - BITS\n",
	"flagged":    "fields:\n  - {name: mask, type: u8}\n  - flagged:\n      field: mask\n      groups:\n        - bit: 0\n          fields:\n            - FIELD\n",
	"definition": "definitions:\n  d:\n    fields:\n      - FIELD\nfields:\n  - $ref: '#/definitions/d'\n",
}

func cr102Schema(context, annotation string) string {
	body := strings.ReplaceAll(cr102Contexts[context], "FIELD", "{name: t, type: u8, "+annotation+"}")
	body = strings.ReplaceAll(body, "BITS", "{name: t, type: 'u8[0:7]', "+annotation+"}")
	return "name: p\n" + body
}

func TestCR102NonMappingAnnotationIsRefused(t *testing.T) {
	refused := map[string]string{
		"ipso: 3303":             cr102IpsoMessage,
		"ipso: '3303'":           cr102IpsoMessage,
		"ipso: null":             cr102IpsoMessage,
		"ipso: [3303]":           cr102IpsoMessage,
		"ipso: {resource: 5700}": cr102IpsoMessage,
		"senml: 'Cel'":           cr102SenmlMessage,
		"senml: null":            cr102SenmlMessage,
		"senml: ['Cel']":         cr102SenmlMessage,
	}
	for context := range cr102Contexts {
		for annotation, message := range refused {
			_, err := ParseSchema(cr102Schema(context, annotation))
			if err == nil || !strings.Contains(err.Error(), message) {
				t.Errorf("%s, %s: error %v, want %q", context, annotation, err, message)
			}
		}
	}
}

func TestCR102MappingAnnotationIsAccepted(t *testing.T) {
	accepted := []string{
		"ipso: {object: 3303}",
		"ipso: {object: 3303, instance: 1, resource: 5700}",
		"senml: {unit: Cel}",
		"senml: {name: x, unit: Cel}",
	}
	for context := range cr102Contexts {
		for _, annotation := range accepted {
			if _, err := ParseSchema(cr102Schema(context, annotation)); err != nil {
				t.Errorf("%s, %s: %v", context, annotation, err)
			}
		}
	}
}

func TestCR102LegacySemanticIpsoIsOutsidePS510(t *testing.T) {
	_, err := ParseSchema("name: p\nfields:\n  - {name: t, type: u8, semantic: {ipso: 3303}}\n")
	if err != nil && strings.Contains(err.Error(), "PS-510") {
		t.Errorf("semantic.ipso refused under PS-510: %v", err)
	}
}
