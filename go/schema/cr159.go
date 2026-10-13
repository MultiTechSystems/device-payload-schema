package schema

import (
	"errors"
	"fmt"
)

// CR-2026-159: constructs withdrawn for want of any use or of any test, rejected at load
// with what replaces them. The texts are the specification prototype's WITHDRAWN table
// and the `min`/`max` message of its check_field, word for word.
const (
	withdrawnFormula = "`formula` is withdrawn; use `compute`, `polynomial`, `transform` or `guard` " +
		"(CR-2026-159, PS-448)"
	withdrawnEncodeFormula = "`encode_formula` is withdrawn with `formula` (CR-2026-159, PS-448)"
	withdrawnClamp         = "`clamp` is withdrawn; use a `floor` stage and a `ceiling` stage " +
		"(CR-2026-159, PS-115)"
	withdrawnConfirmed = "`confirmed` is withdrawn: confirmed or unconfirmed is chosen per " +
		"transmission, not by the schema (CR-2026-159, PS-448)"
)

// checkWithdrawnField rejects `formula`, `encode_formula`, and `min` or `max` on a field
// that is no repeat (they bound a repeat's element count; a value range is
// `valid_range`, PS-395). A transform's `clamp` is checkTransformStages'.
func checkWithdrawnField(field map[string]any) error {
	if _, ok := field["formula"]; ok {
		return errors.New(withdrawnFormula)
	}
	if _, ok := field["encode_formula"]; ok {
		return errors.New(withdrawnEncodeFormula)
	}
	if field["type"] != "repeat" {
		name, ok := field["name"].(string)
		if !ok {
			name = "<unnamed>"
		}
		for _, bound := range []string{"min", "max"} {
			if _, has := field[bound]; has {
				return fmt.Errorf("`%s` on field '%s' is withdrawn: it bounds a repeat's element "+
					"count only; declare a value range with `valid_range` (CR-2026-159, PS-395)",
					bound, name)
			}
		}
	}
	return nil
}

// checkWithdrawnPorts rejects a port's `confirmed`.
func checkWithdrawnPorts(raw map[string]any) error {
	ports := asStringMap(raw["ports"])
	for _, key := range sortedKeys(ports) {
		if port := asStringMap(ports[key]); port != nil {
			if _, has := port["confirmed"]; has {
				return errors.New(withdrawnConfirmed)
			}
		}
	}
	return nil
}
