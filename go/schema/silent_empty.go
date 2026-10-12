package schema

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// No silent empty encode (PS-442, PS-551). An encoder never returns success having
// written nothing for a construct whose decode reads bytes, nor an empty payload where
// the input gives a value only payload bytes carry. Mirrors tools/schema_interpreter.py:
// _body_score, _default_body, _best_case, declared_sources and empty_payload_error.

// bodyScore is how many of a match branch's reported names the input gives, through
// every construct a decode of the branch reports from (a byte_group's or flagged group's
// members, a nested match's best branch, a tlv's cases); ok is false where the input
// rules the branch out: a literal the input gives with another value (PS-359, PS-513),
// or a nested match every branch of which is ruled out and whose default is not `skip`.
// A computed field is not counted. Only the top level of a branch used to be counted, so
// a branch whose fields sit in another construct scored nothing, no case was chosen, and
// the match wrote no bytes and reported nothing.
func bodyScore(body []Field, data map[string]any) (int, bool) {
	score := 0
	for _, f := range body {
		if len(f.ByteGroup) > 0 {
			hits, ok := bodyScore(f.ByteGroup, data)
			if !ok {
				return 0, false
			}
			score += hits
			continue
		}
		if f.Flagged != nil {
			for _, g := range f.Flagged.Groups {
				hits, _ := bodyScore(g.Fields, data)
				score += hits
			}
			continue
		}
		if f.MatchInline != nil {
			m := *f.MatchInline
			if m.Name != "" {
				if _, ok := data[m.Name]; ok {
					score++
				}
			}
			var bodies [][]Field
			for _, c := range m.Cases {
				if !c.Default {
					bodies = append(bodies, c.Fields)
				}
			}
			if d, ok := defaultBody(m); ok {
				bodies = append(bodies, d)
			}
			best, seen := 0, false
			for _, b := range bodies {
				if hits, ok := bodyScore(b, data); ok && (!seen || hits > best) {
					best, seen = hits, true
				}
			}
			if seen {
				score += best
			} else if m.MatchDefault != "skip" {
				return 0, false
			}
			continue
		}
		if tlv := f.TLVInline; tlv != nil || f.Type == "tlv" {
			if tlv == nil {
				tlv = &f
			}
			names := map[string]bool{}
			for _, caseFields := range tlv.TLVCases {
				for _, c := range caseClaims(caseFields, false, nil) {
					names[c.field.Name] = true
				}
			}
			for n := range names {
				if _, ok := data[n]; ok {
					score++
				}
			}
			continue
		}
		if f.Name == "" || strings.HasPrefix(f.Name, "_") ||
			(isComputedField(f) && !isLiteralField(f)) {
			continue
		}
		key, err := resolveEncodeName(f, body, data)
		if err != nil {
			continue
		}
		value, given := data[key]
		if !given {
			continue
		}
		if isLiteralField(f) && !sameLabel(f.Value, value) {
			return 0, false
		}
		score++
	}
	return score, true
}

// defaultBody is a match's default field list - `default:` beside its cases, or a
// `default` case - and false where its default is `error` or `skip`.
func defaultBody(m Field) ([]Field, bool) {
	switch fallback := m.MatchDefault.(type) {
	case []any:
		return parseFieldsRaw(fallback), true
	case []Field:
		return fallback, true
	}
	for _, c := range m.Cases {
		if c.Default {
			return c.Fields, true
		}
	}
	return nil, false
}

// bestCase is the case whose fields the input gives most of, scored by bodyScore: the
// first of equal cases in declaration order wins (PS-563), and tied says another
// equalled it. ok is false where none scores, or where the match's default list explains
// more of the input than any case: the default is what a decoder takes for every value no
// case lists. Go keeps a match's Cases sorted for decoding; declaredCases gives the order
// written, from the schema's keyOrder.
func bestCase(m Field, data map[string]any, order keyOrder) (Case, bool, bool) {
	var best Case
	found, bestHits, tied := false, 0, false
	for _, c := range declaredCases(m, order) {
		if c.Default {
			continue
		}
		hits, _ := bodyScore(c.Fields, data)
		if hits > bestHits {
			best, found, bestHits, tied = c, true, hits, false
		} else if hits > 0 && hits == bestHits {
			tied = true
		}
	}
	if !found {
		return Case{}, false, false
	}
	if d, ok := defaultBody(m); ok {
		if hits, _ := bodyScore(d, data); hits > bestHits {
			return Case{}, false, false
		}
	}
	return best, tied, true
}

// declaredSource is what declared_sources records for one name: whether some
// declaration reads payload bytes or computes from them, and the values its literals give.
type declaredSource struct {
	reads    bool
	literals []any
}

// declaredSources is every name a field list declares, anywhere in it, walked over the
// declarations as written: a mapping with `name` and `type`, or a match's `name`.
func declaredSources(fields []Field) map[string]*declaredSource {
	found, _ := declaredSourcesInOrder(fields, nil)
	return found
}

// declaredSourcesInOrder is declaredSources with the names in the order the schema first
// declares them, its mappings walked in source order (keyOrder).
func declaredSourcesInOrder(fields []Field, order keyOrder) (map[string]*declaredSource, []string) {
	found := map[string]*declaredSource{}
	var names []string
	note := func(name string, literal bool, value any) {
		entry := found[name]
		if entry == nil {
			entry = &declaredSource{}
			found[name] = entry
			names = append(names, name)
		}
		if literal {
			entry.literals = append(entry.literals, value)
		} else {
			entry.reads = true
		}
	}
	var walk func(node any)
	walk = func(node any) {
		switch n := node.(type) {
		case []any:
			for _, item := range n {
				walk(item)
			}
		case []map[string]any:
			for _, item := range n {
				walk(item)
			}
		case map[string]any:
			name, _ := n["name"].(string)
			if _, typed := n["type"]; typed && name != "" {
				t, _ := n["type"].(string)
				_, hasValue := n["value"]
				if hasValue && (t == "string" || t == "number" || t == "boolean") {
					note(name, true, n["value"])
				} else {
					note(name, false, nil)
				}
			}
			if m, ok := n["match"].(map[string]any); ok {
				if mn, ok := m["name"].(string); ok {
					note(mn, false, nil)
				}
			}
			for _, key := range order.keys(n) {
				switch v := n[key]; v.(type) {
				case []any, map[string]any, []map[string]any:
					walk(v)
				}
			}
		}
	}
	for _, f := range fields {
		if f.decl != nil {
			walk(f.decl)
		}
	}
	return found, names
}

// emptyPayloadError is PS-442's last line: an encoder that wrote no bytes, where the
// input gives a value that only a field reading payload bytes (or computing from them)
// reports, dropped that field's bytes without a word. A value that can be written in no
// bytes is not one: an empty list, mapping or string, a value a literal of that name
// gives, and an internal or absent one. The reference reports the first such name in
// the input's order; a Go map has none, so the names are taken in the order the schema
// declares them (keyOrder), which is a decoded input's order too (PS-563's note).
func emptyPayloadError(fields []Field, data map[string]any, order keyOrder) error {
	sources, keys := declaredSourcesInOrder(fields, order)
	for _, key := range keys {
		if _, given := data[key]; !given {
			continue
		}
		value := data[key]
		if strings.HasPrefix(key, "_") || value == nil {
			continue
		}
		switch v := value.(type) {
		case string:
			if v == "" {
				continue
			}
		case []any:
			if len(v) == 0 {
				continue
			}
		case map[string]any:
			if len(v) == 0 {
				continue
			}
		case []byte:
			if len(v) == 0 {
				continue
			}
		}
		entry := sources[key]
		if entry == nil || !entry.reads {
			continue
		}
		explained := false
		for _, literal := range entry.literals {
			if sameLabel(literal, value) {
				explained = true
				break
			}
		}
		if explained {
			continue
		}
		return fmt.Errorf("Encoding wrote no bytes, though the input gives '%s', which the "+
			"schema reads from the payload (PS-442)", key)
	}
	return nil
}

var templateVarPattern = regexp.MustCompile(`\$\{\w+\}`)

// nameFromKeyError: a `name_from` field reports under a key built from its template,
// which no claim can name. Where no field declares that key by name, no case takes it,
// and it was dropped with nothing reported (rakwireless's `digital_in_${ch}`, whose tag
// is not in the data).
func nameFromKeyError(field Field, data map[string]any, claimed func(string) bool,
	scope []Field) error {
	keys := make([]string, 0, len(data))
	for k := range data {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	caseKeys := make([]string, 0, len(field.TLVCases))
	for k := range field.TLVCases {
		caseKeys = append(caseKeys, k)
	}
	sort.Strings(caseKeys)
	var declared map[string]*declaredSource
	for _, caseKey := range caseKeys {
		for _, c := range caseClaims(field.TLVCases[caseKey], false, nil) {
			if c.field.NameFrom == "" {
				continue
			}
			parts := templateVarPattern.Split(c.field.NameFrom, -1)
			for i, p := range parts {
				parts[i] = regexp.QuoteMeta(p)
			}
			pattern := regexp.MustCompile("^" + strings.Join(parts, ".+") + "$")
			for _, key := range keys {
				if claimed(key) || !pattern.MatchString(key) {
					continue
				}
				if declared == nil {
					if scope == nil {
						scope = []Field{field}
					}
					declared = declaredSources(scope)
				}
				if declared[key] != nil {
					continue
				}
				return fmt.Errorf("tlv: no case takes the value of '%s' %s in full (PS-502, PS-527)",
					key, pyRepr(data[key]))
			}
		}
	}
	return nil
}

// nonNilFields is a case body as chosen: an empty case (`0: []`) is a body that reads
// nothing, not the absence of a choice.
func nonNilFields(fields []Field) []Field {
	if fields == nil {
		return []Field{}
	}
	return fields
}

// defaultScores reports whether a match's default list explains any of the input: a
// `remaining` match then takes its default, not the case matching 0 bytes.
func defaultScores(m Field, data map[string]any) bool {
	d, ok := defaultBody(m)
	if !ok {
		return false
	}
	hits, _ := bodyScore(d, data)
	return hits > 0
}
