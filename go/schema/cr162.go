// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

// CR-2026-162 (PS-572 to PS-574): the decode record.
//
// Interpret with InputContext.DecodeRecord adds `_decode`: the location of every match
// evaluated with the case it took and the discriminator it read, every internal field
// decoded with its value, and every flagged construct with its flags. A location is the
// construct's RFC 6901 JSON Pointer into the $ref-expanded document, with `/repeat[i]`
// after a repeat field's own pointer, `tlv[k]` for a tlv entry in payload order and
// `records/n[0]` for a record. An encoder given the record follows it (PS-573): each
// recorded case, discriminator, internal value and flags value is used where the input
// gives none, and input contradicting it is rejected. Where neither gives a match's case,
// this encoder derives it (PS-551, PS-563, PS-564), as PS-574 permits.
//
// A port of DecodeTrace, pointer_map, _recorded_inputs, _recorded_flags,
// _recorded_case, _check_recorded_case in
// tools/schema_interpreter.py. Every mapping of the expanded document is a fresh
// map[string]any (expandRawRefs copies each splice), and each Field keeps its own as
// decl, so a mapping's identity locates it.

import (
	"fmt"
	"math"
	"reflect"
	"sort"
	"strings"
)

// pointerMap is each mapping of the expanded document by identity, with its pointer.
type pointerMap map[uintptr]pointerEntry

type pointerEntry struct {
	node    any // keeps the address from being reused while the entry stands
	pointer string
}

// pointerSegment is one RFC 6901 reference token: `~` as `~0`, `/` as `~1`.
func pointerSegment(key string) string {
	return strings.ReplaceAll(strings.ReplaceAll(key, "~", "~0"), "/", "~1")
}

// buildPointerMap walks the document's header, fields and ports, as pointer_map does.
func buildPointerMap(raw map[string]any) pointerMap {
	found := pointerMap{}
	var walk func(node any, pointer string)
	walk = func(node any, pointer string) {
		switch typed := node.(type) {
		case map[string]any:
			if id, ok := mapIdentity(typed); ok {
				if _, seen := found[id]; !seen {
					found[id] = pointerEntry{typed, pointer}
				}
			}
			for key, value := range typed {
				walk(value, pointer+"/"+pointerSegment(key))
			}
		case []any:
			for i, value := range typed {
				walk(value, fmt.Sprintf("%s/%d", pointer, i))
			}
		}
	}
	for _, key := range []string{"header", "fields", "ports"} {
		if value, ok := raw[key]; ok && value != nil {
			walk(value, "/"+key)
		}
	}
	return found
}

func (p pointerMap) of(m map[string]any) (string, bool) {
	if m == nil {
		return "", false
	}
	id, ok := mapIdentity(m)
	if !ok {
		return "", false
	}
	entry, ok := p[id]
	if !ok || reflect.ValueOf(entry.node).Pointer() != id {
		return "", false
	}
	return entry.pointer, true
}

type traceFrame struct {
	anchor string
	kind   string
	index  int
}

// decodeTrace is the decode record, kept while decoding and followed while encoding.
type decodeTrace struct {
	pointers pointerMap
	frames   []traceFrame
	matches  map[string]any
	internal map[string]any
	flagged  map[string]any
	// supplied names the values the record, not the input, gave; inputs the names the
	// encode's input gives (encode only).
	supplied map[string]bool
	inputs   map[string]bool
}

func newDecodeTrace(pointers pointerMap, record map[string]any) *decodeTrace {
	t := &decodeTrace{pointers: pointers, matches: map[string]any{}, internal: map[string]any{},
		flagged: map[string]any{}, supplied: map[string]bool{}, inputs: map[string]bool{}}
	if record != nil {
		for table, into := range map[string]map[string]any{"matches": t.matches,
			"internal": t.internal, "flagged": t.flagged} {
			for k, v := range asStringMap(record[table]) {
				into[k] = v
			}
		}
	}
	return t
}

// pushNode opens an occurrence frame anchored at a mapping's pointer plus suffix.
func (t *decodeTrace) pushNode(node map[string]any, suffix, kind string, index int) bool {
	if t == nil {
		return false
	}
	anchor, ok := t.pointers.of(node)
	if !ok {
		return false
	}
	t.frames = append(t.frames, traceFrame{anchor + suffix, kind, index})
	return true
}

// pushTLV opens a tlv entry's frame. Only a `- tlv:` block is located: the reference
// reads the `tlv` key of the field, which a `type: tlv` field does not have.
func (t *decodeTrace) pushTLV(field Field, index int) bool {
	if t == nil || !field.tlvInline {
		return false
	}
	return t.pushNode(field.decl, "", "tlv", index)
}

func (t *decodeTrace) pop(pushed bool) {
	if pushed {
		t.frames = t.frames[:len(t.frames)-1]
	}
}

// loc is the location of a mapping in the current occurrence frames (DecodeTrace.loc).
func (t *decodeTrace) loc(node map[string]any) (string, bool) {
	pointer, ok := t.pointers.of(node)
	if !ok {
		return "", false
	}
	frames := append([]traceFrame{}, t.frames...)
	sort.SliceStable(frames, func(i, j int) bool { return len(frames[i].anchor) > len(frames[j].anchor) })
	for _, f := range frames {
		if pointer == f.anchor || strings.HasPrefix(pointer, f.anchor+"/") {
			rest := pointer[len(f.anchor):]
			if f.kind == "repeat" {
				pointer = fmt.Sprintf("%s/repeat[%d]%s", f.anchor, f.index, rest)
			} else {
				pointer = fmt.Sprintf("%s[%d]%s", f.anchor, f.index, rest)
			}
		}
	}
	return pointer, true
}

func (t *decodeTrace) lookup(table map[string]any, node map[string]any) (any, bool) {
	if t == nil {
		return nil, false
	}
	where, ok := t.loc(node)
	if !ok {
		return nil, false
	}
	v, ok := table[where]
	return v, ok
}

func (t *decodeTrace) asMap() map[string]any {
	return map[string]any{"matches": t.matches, "internal": t.internal, "flagged": t.flagged}
}

// recordedValue is a value as the record reports it (normalize_output): an integral
// number as an integer, bytes as hex; false for one that is not reported (NaN).
func recordedValue(v any) (any, bool) {
	switch t := v.(type) {
	case nil:
		return nil, false
	case float64:
		if math.IsNaN(t) || math.IsInf(t, 0) {
			return nil, false
		}
		if t == math.Trunc(t) && math.Abs(t) < 1e18 {
			return int64(t), true
		}
	case float32:
		return recordedValue(float64(t))
	case []byte:
		return fmt.Sprintf("%x", t), true
	}
	if v == omitted || v == absentSentinel {
		return nil, false
	}
	return v, true
}

func (ctx *DecodeContext) recordMatch(field Field, key any, value any) {
	if ctx.trace == nil || field.matchDecl == nil {
		return
	}
	where, ok := ctx.trace.loc(field.matchDecl)
	if !ok {
		return
	}
	if text, isText := key.(string); isText && text != "default" {
		if n, err := parseIntAny(text); err == nil && fmt.Sprint(n) == text {
			key = n
		}
	}
	reported, _ := recordedValue(value)
	ctx.trace.matches[where] = map[string]any{"case": key, "value": reported}
}

func (ctx *DecodeContext) recordFlags(decl map[string]any, flags int) {
	if ctx.trace == nil || decl == nil {
		return
	}
	if where, ok := ctx.trace.loc(decl); ok {
		ctx.trace.flagged[where] = flags
	}
}

// recordInternal records an internal field's decoded value (what it would report).
func (ctx *DecodeContext) recordInternal(decl map[string]any, value any) {
	if ctx.trace == nil || decl == nil {
		return
	}
	reported, ok := recordedValue(value)
	if !ok {
		return
	}
	if where, ok := ctx.trace.loc(decl); ok {
		ctx.trace.internal[where] = reported
	}
}

// recordGroupInternal records an internal byte_group member as the reference does: a
// bool its flag, any other member the number its name binds.
func (ctx *DecodeContext) recordGroupInternal(member Field, isBool bool, value any) {
	if ctx.trace == nil || !strings.HasPrefix(member.Name, "_") {
		return
	}
	if isBool {
		if value != omitted {
			ctx.recordInternal(member.decl, value)
		}
		return
	}
	if bound, ok := ctx.Variables[member.Name]; ok {
		ctx.recordInternal(member.decl, bound)
	}
}

// --- encoding ------------------------------------------------------------------

// EncodeWithRecord is EncodeWithPort following a decode record (PS-573): the `_decode`
// Interpret produced with DecodeRecord. A record in data as `_decode` is followed too;
// the argument wins. `_decode` and `_meta` are never input.
func (s *Schema) EncodeWithRecord(data map[string]any, fPort int, record map[string]any) ([]byte, error) {
	ctx, err := s.encodeToContextRecord(data, fPort, nil, record)
	if err != nil {
		return nil, err
	}
	return ctx.Buffer, nil
}

// EncodeOrderedWithRecord is EncodeOrderedWithPort following a decode record.
func (s *Schema) EncodeOrderedWithRecord(data map[string]any, fPort int, order []string,
	record map[string]any) ([]byte, error) {
	ctx, err := s.encodeToContextRecord(data, fPort, order, record)
	if err != nil {
		return nil, err
	}
	return ctx.Buffer, nil
}

// takeRecord strips `_decode` and `_meta` from the input and returns the record to follow.
func (s *Schema) takeRecord(data map[string]any, record map[string]any) (map[string]any, *decodeTrace) {
	_, hasRecord := data["_decode"]
	_, hasMeta := data["_meta"]
	if hasRecord || hasMeta {
		if record == nil {
			record = asStringMap(data["_decode"])
		}
		stripped := make(map[string]any, len(data))
		for k, v := range data {
			if k != "_decode" && k != "_meta" {
				stripped[k] = v
			}
		}
		data = stripped
	}
	if record == nil {
		return data, nil
	}
	trace := newDecodeTrace(s.pointers, record)
	for k := range data {
		trace.inputs[k] = true
	}
	return data, trace
}

// sameRecorded is Python's `==` on two JSON values: numbers by value.
func sameRecorded(a, b any) bool {
	_, aBool := a.(bool)
	_, bBool := b.(bool)
	if !aBool && !bBool {
		if x, ok := toFloat64(a); ok {
			if y, ok := toFloat64(b); ok {
				return x == y
			}
		}
	}
	return reflect.DeepEqual(a, b)
}

// messageValue writes a value quoted in a PS-573 message (CR-2026-164): a number as
// JavaScript's String(number) writes it (`2`, not `2.0`), anything else as Python's repr.
func messageValue(v any) string {
	switch v.(type) {
	case int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64, float32, float64:
		return givenText(v)
	}
	return pyRepr(v)
}

// recordedInputs is _recorded_inputs: what the decode record supplies for this field
// list - each internal field's recorded value where the input does not give it (an input
// giving another value is rejected), and each flagged construct's recorded flags under its
// flags field where the input gives it none. A byte_group's members are looked into.
func recordedInputs(fields []Field, data, derived map[string]any, t *decodeTrace) error {
	internal := func(f Field) error {
		if !strings.HasPrefix(f.Name, "_") || isComputedField(f) || isLiteralField(f) {
			return nil
		}
		if _, hasValue := f.decl["value"]; hasValue {
			return nil
		}
		rec, ok := t.lookup(t.internal, f.decl)
		if !ok {
			return nil
		}
		if given, present := data[f.Name]; present && !t.supplied[f.Name] {
			if !sameRecorded(given, rec) {
				return fmt.Errorf("Field '%s' is given %s, but the decode record holds %s (PS-573)",
					f.Name, messageValue(given), messageValue(rec))
			}
			return nil
		}
		derived[f.Name] = rec
		t.supplied[f.Name] = true
		return nil
	}
	for _, f := range fields {
		if len(f.ByteGroup) > 0 {
			for _, m := range f.ByteGroup {
				if err := internal(m); err != nil {
					return err
				}
			}
			continue
		}
		if f.Flagged != nil {
			mask := strings.TrimPrefix(f.Flagged.Field, "$")
			rec, ok := t.lookup(t.flagged, f.Flagged.decl)
			if mask != "" && ok && data[mask] == nil && derived[mask] == nil {
				derived[mask] = rec
			}
			continue
		}
		if err := internal(f); err != nil {
			return err
		}
	}
	return nil
}

// recordedFlags is _recorded_flags: the flags the record holds for a flagged construct,
// where the input gives its flags field no value.
func recordedFlags(fd *FlaggedDef, ctx *EncodeContext) (int, bool) {
	t := ctx.trace
	if t == nil {
		return 0, false
	}
	mask := strings.TrimPrefix(fd.Field, "$")
	if t.inputs[mask] {
		return 0, false
	}
	rec, ok := t.lookup(t.flagged, fd.decl)
	if !ok {
		return 0, false
	}
	if _, isBool := rec.(bool); isBool {
		return 0, false
	}
	f, ok := toFloat64(rec)
	if !ok {
		return 0, false
	}
	return int(math.Floor(f)), true
}

// recordMatchLabel is match_label: `'name'`, else `'var'`, else "an unnamed match".
func recordMatchLabel(field Field) string {
	if field.Name != "" {
		return "'" + field.Name + "'"
	}
	if field.Var != "" {
		return "'" + field.Var + "'"
	}
	return "an unnamed match"
}

// recordedCase is _recorded_case: the case a record names, "default" or the key as
// written; a key the match does not have is rejected.
func recordedCase(field Field, declared []Case, key any) (Case, bool, error) {
	if text, ok := key.(string); ok && text == "default" {
		return Case{}, true, nil
	}
	want := strings.TrimSpace(fmt.Sprint(normaliseRecordedKey(key)))
	for _, c := range declared {
		if !c.Default && strings.TrimSpace(fmt.Sprint(c.Case)) == want {
			return c, false, nil
		}
	}
	return Case{}, false, fmt.Errorf("Field %s: the recorded case %s is not a case of the match (PS-573)",
		recordMatchLabel(field), messageValue(key))
}

func normaliseRecordedKey(key any) any {
	if f, ok := key.(float64); ok && f == math.Trunc(f) {
		return int64(f)
	}
	return key
}

// recordDeclared is record_declared_fields over the declarations as written: every name a
// field list can report, through the constructs whose fields merge into the enclosing
// object. A match's `default` list beside its cases is looked into too, where the
// prototype's is not. skip leaves out one entry, matched by the identity of its `match`
// mapping.
func recordDeclared(items any, skip map[string]any, found map[string]bool) {
	list, _ := items.([]any)
	for _, raw := range list {
		item := asStringMap(raw)
		if item == nil {
			continue
		}
		if skip != nil {
			if m := asStringMap(item["match"]); m != nil && sameMap(m, skip) {
				continue
			}
		}
		if _, typed := item["type"]; typed {
			if name, ok := item["name"].(string); ok && name != "" {
				found[name] = true
			}
			recordDeclared(item["trailer"], skip, found)
			continue
		}
		var spec any
		for _, k := range []string{"match", "tlv", "flagged", "byte_group", "records"} {
			if v, ok := item[k]; ok {
				spec = v
				break
			}
		}
		switch typed := spec.(type) {
		case []any:
			for _, part := range typed {
				if sub, ok := part.([]any); ok {
					recordDeclared(sub, skip, found)
				} else {
					recordDeclared([]any{part}, skip, found)
				}
			}
		default:
			if m := asStringMap(spec); m != nil {
				recordDeclared(m["fields"], skip, found)
				for _, body := range asStringMap(m["cases"]) {
					recordDeclared(body, skip, found)
				}
				if def, ok := m["default"].([]any); ok {
					recordDeclared(def, skip, found)
				}
				if groups, ok := m["groups"].([]any); ok {
					for _, g := range groups {
						recordDeclared(asStringMap(g)["fields"], skip, found)
					}
				}
			}
		}
	}
}

func sameMap(a, b map[string]any) bool {
	x, okA := mapIdentity(a)
	y, okB := mapIdentity(b)
	return okA && okB && x == y
}

// checkRecordedCase is _check_recorded_case: under a case taken from the record, the input
// may not give a field only another case of the match declares. A name something outside
// the match declares within the same reported object - the other entries of the lists
// enclosing it - is not one (CR-2026-165). The match's `default` list is one of its
// cases (CR-2026-164).
func checkRecordedCase(field Field, chosenKey string, data map[string]any, ctx *EncodeContext) error {
	decl := field.matchDecl
	cases := asStringMap(decl["cases"])
	var defaultBody any
	if d, ok := decl["default"].([]any); ok {
		defaultBody = d
	} else if d, ok := cases["default"].([]any); ok {
		defaultBody = d
	}
	isCase := chosenKey != "" && chosenKey != "default"
	chosenRaw := ""
	if isCase {
		for key := range cases {
			if key == chosenKey || strings.TrimSpace(key) == strings.TrimSpace(chosenKey) {
				chosenRaw = key
				break
			}
		}
		if chosenRaw == "" {
			// A case key Go wrote in another spelling (an integer `0x10`): by value.
			for key := range cases {
				if n, err := parseIntAny(key); err == nil && fmt.Sprint(n) == chosenKey {
					chosenRaw = key
					break
				}
			}
		}
	}
	mine := map[string]bool{}
	if isCase && chosenRaw != "" {
		recordDeclared(cases[chosenRaw], nil, mine)
	} else {
		recordDeclared(defaultBody, nil, mine)
	}
	others := map[string]bool{}
	for key, body := range cases {
		if key == "default" || (isCase && key == chosenRaw) {
			continue
		}
		recordDeclared(body, nil, others)
	}
	if isCase && defaultBody != nil {
		recordDeclared(defaultBody, nil, others)
	}
	outside := map[string]bool{}
	for _, e := range ctx.enclosing {
		rest := make([]Field, 0, len(e.fields))
		for i, f := range e.fields {
			if i != e.cur {
				rest = append(rest, f)
			}
		}
		recordDeclared(fieldDecls(rest), nil, outside)
	}
	var clash []string
	for name := range others {
		if mine[name] || outside[name] {
			continue
		}
		if _, given := data[name]; given {
			clash = append(clash, name)
		}
	}
	if len(clash) > 0 {
		sort.Strings(clash)
		return fmt.Errorf("Field %s: the input gives '%s', which only another case declares, against the recorded case (PS-573)",
			recordMatchLabel(field), clash[0])
	}
	return nil
}

// undeterminedCaseError is undetermined_case_message (PS-574): a match whose case neither
// the input nor a decode record gives, and which this encoder could not derive either.
func undeterminedCaseError(name string) error {
	return fmt.Errorf("Missing required field: %s (PS-574)", name)
}
