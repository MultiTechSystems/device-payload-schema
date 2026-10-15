package schema

import "fmt"

// CR-2026-185 (PS-585 amended, PS-608): a checksum's `from` counts from the frame's first
// byte wherever the field sits, so a checksum field is rejected at load where it is a
// byte_group member, or sits within a tlv entry, a records entry or a repeat's `fields` or
// `element` - each decoded or encoded apart from the frame, or repeated. A repeat's
// `trailer` is read at its frame offset (PS-383) and is allowed. The spec prototype's
// check_checksum_placement, in its walk order and words.

// checksumExcluded names the constructs a checksum may not sit within (CHECKSUM_EXCLUDED).
var checksumExcluded = map[string]string{
	"byte_group": "a byte_group",
	"tlv":        "a tlv entry",
	"records":    "a records entry",
}

// checkChecksumPlacement walks the header, fields and ports of the `$ref`-spliced document
// and returns the first PS-608 violation.
func checkChecksumPlacement(raw map[string]any, order keyOrder) error {
	var walk func(node any, inside string) error
	walk = func(node any, inside string) error {
		switch n := node.(type) {
		case []any:
			for _, item := range n {
				if err := walk(item, inside); err != nil {
					return err
				}
			}
		case map[string]any, map[any]any:
			m := asStringMap(n)
			_, hasChecksum := m["checksum"]
			_, hasType := m["type"]
			if hasChecksum && hasType && inside != "" {
				name := "None"
				if s, ok := m["name"].(string); ok {
					name = s
				}
				return fmt.Errorf("Field '%s': a checksum field cannot sit within %s; its `from` counts from the frame (PS-608)",
					name, inside)
			}
			repeat := m["type"] == "repeat"
			for _, key := range order.keys(n) {
				if key == "checksum" {
					continue
				}
				within := inside
				if w, ok := checksumExcluded[key]; ok {
					within = w
				}
				if repeat && (key == "fields" || key == "element") {
					within = "a repeat element"
				}
				if err := walk(m[key], within); err != nil {
					return err
				}
			}
		}
		return nil
	}
	for _, key := range []string{"header", "fields", "ports"} {
		if err := walk(raw[key], ""); err != nil {
			return err
		}
	}
	return nil
}
