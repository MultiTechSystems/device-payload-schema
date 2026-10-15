// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

// CR-2026-187 (PS-616 to PS-618): `bit_labels` on an unsigned integer or unsigned bit range
// reports the labels of its set bits, as a list or, with `join`, as one text. Mirrors
// bit_labels_error, decode_bit_labels and encode_bit_labels in tools/schema_interpreter.py,
// in the spec prototype's text.

var bitLabelKeys = []string{"bit_labels", "join", "none", "order"}

// bitLabelExcluded are the keys PS-616 rejects beside bit_labels, in the prototype's order.
var bitLabelExcluded = []string{"lookup", "add", "mult", "div", "transform", "polynomial",
	"time", "compute", "value", "encoding"}

var bitLabelWholeWidths = map[string]int{"u8": 8, "u16": 16, "u24": 24, "u32": 32, "u64": 64,
	"uint8": 8, "uint16": 16, "uint24": 24, "uint32": 32, "uint64": 64}

type bitLabel struct {
	key   any // as written, for a message
	bit   int
	isBit bool
	label any
}

// bitLabelWidth is the width in bits of an unsigned integer type or unsigned bit range.
func bitLabelWidth(typ any) (int, bool) {
	t, ok := typ.(string)
	if !ok {
		return 0, false
	}
	t = strings.TrimSpace(t)
	if w, ok := bitLabelWholeWidths[t]; ok {
		return w, true
	}
	if m := bitRangePattern.FindStringSubmatch(t); m != nil && strings.HasPrefix(t, "u") {
		start, _ := strconv.Atoi(m[2])
		end, _ := strconv.Atoi(m[3])
		if end >= start {
			return end - start + 1, true
		}
	}
	return 0, false
}

// bitLabelEntries reads a bit_labels mapping, whichever key type the YAML or JSON reader
// gave it, sorted by bit number (keys that are no bit number last, in text order).
func bitLabelEntries(raw any) ([]bitLabel, bool) {
	var out []bitLabel
	add := func(k, v any) {
		e := bitLabel{key: k, label: v}
		switch kv := k.(type) {
		case int:
			e.bit, e.isBit = kv, true
		case int64:
			e.bit, e.isBit = int(kv), true
		case uint64:
			e.bit, e.isBit = int(kv), kv < 1<<31
		case float64:
			if kv == math.Trunc(kv) {
				e.bit, e.isBit = int(kv), true
			}
		case string:
			if kv != "" && strings.Trim(kv, "0123456789") == "" {
				if n, err := strconv.Atoi(kv); err == nil {
					e.bit, e.isBit, e.key = n, true, n
				}
			}
		}
		out = append(out, e)
	}
	switch m := raw.(type) {
	case map[string]any:
		for k, v := range m {
			add(k, v)
		}
	case map[any]any:
		for k, v := range m {
			add(k, v)
		}
	case map[int]any:
		for k, v := range m {
			add(k, v)
		}
	default:
		return nil, false
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].isBit != out[j].isBit {
			return out[i].isBit
		}
		if out[i].isBit {
			return out[i].bit < out[j].bit
		}
		return fmt.Sprint(out[i].key) < fmt.Sprint(out[j].key)
	})
	return out, true
}

// checkBitLabels is PS-616 and PS-617 at load over one field mapping, the first error in the
// prototype's order (check_bit_labels).
func checkBitLabels(field map[string]any) error {
	if _, hasType := field["type"]; !hasType {
		return nil
	}
	declares := false
	for _, k := range bitLabelKeys {
		if _, ok := field[k]; ok {
			declares = true
		}
	}
	if !declares {
		return nil
	}
	where := "Field 'None'"
	if s, ok := field["name"].(string); ok {
		where = "Field '" + s + "'"
	}
	labels, has := field["bit_labels"]
	if !has || labels == nil {
		return fmt.Errorf("%s: join, none and order belong to bit_labels (PS-616)", where)
	}
	width, ok := bitLabelWidth(field["type"])
	if !ok {
		return fmt.Errorf("%s: bit_labels is declared on an unsigned integer or an unsigned "+
			"bit range, not %s (PS-616)", where, pyRepr(field["type"]))
	}
	var clash []string
	for _, k := range bitLabelExcluded {
		if _, ok := field[k]; ok {
			clash = append(clash, k)
		}
	}
	if len(clash) > 0 {
		return fmt.Errorf("%s: bit_labels reports labels, so %s does not apply (PS-616)",
			where, strings.Join(clash, ", "))
	}
	entries, ok := bitLabelEntries(labels)
	if !ok || len(entries) == 0 {
		return fmt.Errorf("%s: bit_labels maps bit numbers to labels (PS-616)", where)
	}
	seen := map[string]bool{}
	var texts []string
	for _, e := range entries {
		if !e.isBit || e.bit < 0 || e.bit >= width {
			shown := pyRepr(e.key)
			if e.isBit {
				shown = strconv.Itoa(e.bit)
			}
			return fmt.Errorf("%s: bit %s is outside its %d bits (PS-617)", where, shown, width)
		}
		text, isText := e.label.(string)
		if !isText {
			return fmt.Errorf("%s: the label of bit %d is a string (PS-616)", where, e.bit)
		}
		if seen[text] {
			return fmt.Errorf("%s: two bits have the label %s (PS-617)", where, pyRepr(text))
		}
		seen[text] = true
		texts = append(texts, text)
	}
	if join, has := field["join"]; has && join != nil {
		j, isText := join.(string)
		if !isText {
			return fmt.Errorf("%s: join is a string (PS-616)", where)
		}
		if j != "" {
			for _, t := range texts {
				if strings.Contains(t, j) {
					return fmt.Errorf("%s: a label contains the join text %s (PS-617)", where, pyRepr(j))
				}
			}
		}
	}
	if none, has := field["none"]; has && none != nil {
		n, isText := none.(string)
		if !isText || seen[n] {
			return fmt.Errorf("%s: none is a string that no bit has as its label (PS-616)", where)
		}
	}
	if order, has := field["order"]; has && order != "ascending" && order != "descending" {
		return fmt.Errorf("%s: order is ascending or descending (PS-616)", where)
	}
	return nil
}

// hasBitLabels reports a field declaring bit_labels.
func hasBitLabels(field Field) bool {
	if field.decl == nil {
		return false
	}
	v, ok := field.decl["bit_labels"]
	return ok && v != nil
}

// bitLabelTable is (bit, label) in the field's declared order (PS-616).
func bitLabelTable(field Field) []bitLabel {
	entries, _ := bitLabelEntries(field.decl["bit_labels"])
	if field.decl["order"] == "descending" {
		for i, j := 0, len(entries)-1; i < j; i, j = i+1, j-1 {
			entries[i], entries[j] = entries[j], entries[i]
		}
	}
	return entries
}

// bitLabelNumber is a decoded value as the unsigned number its bits are read from.
func bitLabelNumber(v any) (uint64, bool) {
	switch n := v.(type) {
	case uint64:
		return n, true
	case int64:
		return uint64(n), n >= 0
	case int:
		return uint64(n), n >= 0
	case uint32:
		return uint64(n), true
	case float64:
		if n >= 0 && n == math.Trunc(n) && n < 1<<64 {
			return uint64(n), true
		}
	}
	return 0, false
}

// decodeBitLabels is PS-616 and PS-617: the labels of the set bits the mapping lists, a set
// bit with no label warned about and not reported.
func decodeBitLabels(field Field, n uint64, ctx *DecodeContext) any {
	table := bitLabelTable(field)
	listed := map[int]bool{}
	for _, e := range table {
		listed[e.bit] = true
	}
	for bit := 0; bit < 64; bit++ {
		if n>>uint(bit)&1 == 1 && !listed[bit] {
			ctx.Warnings = append(ctx.Warnings, fmt.Sprintf(
				"Field '%s': bit %d is set and has no label; it is not reported (PS-617)",
				field.Name, bit))
		}
	}
	labels := []any{}
	for _, e := range table {
		if e.bit >= 0 && e.bit < 64 && n>>uint(e.bit)&1 == 1 {
			labels = append(labels, e.label)
		}
	}
	if none, ok := field.decl["none"].(string); ok && len(labels) == 0 {
		labels = append(labels, none)
	}
	if join, ok := field.decl["join"].(string); ok {
		parts := make([]string, len(labels))
		for i, l := range labels {
			parts[i] = fmt.Sprint(l)
		}
		return strings.Join(parts, join)
	}
	return labels
}

// encodeBitLabels is PS-618: the number with the bits of the given labels set, every other
// bit 0. A list, or the joined text where `join` is declared; an empty one, or the `none`
// label, writes 0. Order and repetition do not matter.
func encodeBitLabels(field Field, value any) (float64, error) {
	name := field.Name
	byLabel := map[string]int{}
	var labels []string
	for _, e := range bitLabelTable(field) {
		if s, ok := e.label.(string); ok {
			byLabel[s] = e.bit
			labels = append(labels, s)
		}
	}
	join, hasJoin := field.decl["join"].(string)
	none, hasNone := field.decl["none"].(string)
	var items []any
	switch v := value.(type) {
	case string:
		if !hasJoin {
			return 0, fmt.Errorf("Field '%s': %s is not a list of labels (PS-618)", name, pyRepr(v))
		}
		switch {
		case v == "" || (hasNone && v == none):
		case join != "":
			for _, part := range strings.Split(v, join) {
				items = append(items, part)
			}
		default:
			sort.SliceStable(labels, func(i, j int) bool { return len(labels[i]) > len(labels[j]) })
			for _, a := range labels {
				for _, b := range labels {
					if a != b && strings.HasPrefix(b, a) {
						return 0, fmt.Errorf("Field '%s': with an empty join, one label is a prefix "+
							"of another, so its text cannot be split; the schema decodes but is "+
							"not encoded (PS-617)", name)
					}
				}
			}
			rest := v
			for rest != "" {
				found := ""
				for _, l := range labels {
					if strings.HasPrefix(rest, l) {
						found = l
						break
					}
				}
				if found == "" {
					return 0, fmt.Errorf("Field '%s': %s does not begin with a label (PS-618)",
						name, pyRepr(rest))
				}
				items = append(items, found)
				rest = rest[len(found):]
			}
		}
	case []any:
		for _, item := range v {
			if s, ok := item.(string); ok && hasNone && s == none {
				continue
			}
			items = append(items, item)
		}
	case []string:
		for _, item := range v {
			if hasNone && item == none {
				continue
			}
			items = append(items, item)
		}
	default:
		suffix := ""
		if hasJoin {
			suffix = " or joined text"
		}
		return 0, fmt.Errorf("Field '%s': %s is not a list of labels%s (PS-618)",
			name, messageValue(value), suffix)
	}
	var n uint64
	for _, item := range items {
		s, ok := item.(string)
		bit, known := byLabel[s]
		if !ok || !known {
			return 0, fmt.Errorf("Field '%s': %s is not one of its labels (PS-618)",
				name, messageValue(item))
		}
		n |= 1 << uint(bit)
	}
	return float64(n), nil
}
