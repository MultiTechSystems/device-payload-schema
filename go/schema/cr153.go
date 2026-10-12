package schema

import "fmt"

// CR-2026-153 (PS-563, PS-564, PS-565), for 0.5.5.
//
// PS-563: where the input gives no discriminator, a tie between cases goes to the case
// declared first. Go keeps a match's Cases sorted (integers ascending, then strings,
// default last), which decoding relies on; on encode the cases are taken in declaration
// order, recovered from the YAML node tree through the schema's keyOrder (buildKeyOrder),
// as _meta and the reference walk take theirs.
//
// PS-564: the bytes a `remaining` match writes, with every byte after it to the end of
// the payload, make the remaining count select the case it chose; otherwise the input is
// rejected, naming the match. Go writes every field list into one buffer, so the count is
// the payload's length less the position the match started at. A match encoded into a
// tlv entry's own buffer, or inside a repeat, is not checked (the reference's
// tail_unchecked).

// remainingCheck is one `remaining` match encoded at the top: where it started and the
// case it chose ("default" for its default).
type remainingCheck struct {
	match    Field
	start    int
	chosen   string
	declared []Case
}

// declaredCases is a match's cases in declaration order. A case the node tree gives no
// order for (a JSON-authored schema) follows in Go's sorted order.
func declaredCases(field Field, order keyOrder) []Case {
	if field.matchDecl == nil {
		return field.Cases
	}
	keys := order.keys(field.matchDecl["cases"])
	if len(keys) == 0 {
		return field.Cases
	}
	out := make([]Case, 0, len(field.Cases))
	used := make([]bool, len(field.Cases))
	for _, key := range keys {
		for i, c := range field.Cases {
			if used[i] {
				continue
			}
			if (c.Default && key == "default") || (!c.Default && fmt.Sprintf("%v", c.Case) == key) {
				out = append(out, c)
				used[i] = true
				break
			}
		}
	}
	for i, c := range field.Cases {
		if !used[i] {
			out = append(out, c)
		}
	}
	return out
}

// remainingSelection is the case key a decoder selects for a `remaining` match with
// count bytes left: the first case in declaration order that matches it, "default" where
// none does and the match declares a default (a list, `skip`, or a `default` case), else
// "" (none). The reference's remaining_selection.
func remainingSelection(field Field, declared []Case, count int) string {
	for _, c := range declared {
		if !c.Default && caseMatchesValue(count, c) {
			return fmt.Sprintf("%v", c.Case)
		}
	}
	if _, ok := defaultBody(field); ok || field.MatchDefault == "skip" {
		return "default"
	}
	return ""
}

// remainingMismatchError is PS-564's rejection, naming the match as PS-535 and PS-557
// name one; the reference's remaining_mismatch_message.
func remainingMismatchError(field Field, count int, selected, chosen string) error {
	label := "an unnamed match"
	if field.Name != "" {
		label = "'" + field.Name + "'"
	}
	describe := func(key string) string {
		switch key {
		case "":
			return "no case"
		case "default":
			return "its default"
		}
		return "case " + key
	}
	return fmt.Errorf("Field %s: the %d byte(s) from it to the end of the payload select %s, "+
		"not %s, the case its input gives (PS-564)", label, count, describe(selected),
		describe(chosen))
}

// checkRemaining holds each `remaining` match encoded at the top to PS-564, once the
// payload is complete.
func (ctx *EncodeContext) checkRemaining() error {
	if ctx.ps564 == nil {
		return nil
	}
	for _, check := range *ctx.ps564 {
		count := len(ctx.Buffer) - check.start
		selected := remainingSelection(check.match, check.declared, count)
		if selected != check.chosen {
			return remainingMismatchError(check.match, count, selected, check.chosen)
		}
	}
	return nil
}
