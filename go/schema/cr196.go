// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"reflect"
	"strings"
)

// CR-2026-196 (PS-635 to PS-637): a `type: number` field declaring `source` reports a value
// of the frame - its FPort, the payload's length, or the bytes from the field's position to
// the end of the data (which ends at a tlv entry's end, as `match: {remaining: true}`
// counts) - reading no bytes, through its modifiers once. With no FPort a `$fPort` field is
// omitted with a warning naming it. An encoder writes nothing for it and checks a given
// value against the finished frame and the FPort it reports. The texts are the spec
// prototype's.

// frameSources are the values `source` may name (PS-635).
var frameSources = []string{"$fPort", "$payloadLength", "$remaining"}

// frameSourceCheck is a `$payloadLength` or `$fPort` field given a value, checked once the
// payload is finished (PS-637).
type frameSourceCheck struct {
	field Field
	given any
}

// checkFrameSource is PS-635's load rule over one field mapping: `source` is one of the
// three, on a `type: number` (or `integer`) field that declares no `value`, `compute`,
// `ref`, `polynomial` or `time` `parts`.
func checkFrameSource(field map[string]any) error {
	typ, hasType := field["type"]
	src, hasSource := field["source"]
	if !hasType || !hasSource {
		return nil
	}
	ok := false
	if t, isText := typ.(string); isText {
		t = strings.ToLower(t)
		ok = t == "number" || t == "integer"
	}
	known := false
	if s, isText := src.(string); isText {
		for _, f := range frameSources {
			known = known || s == f
		}
	}
	clash := false
	for _, k := range []string{"value", "compute", "ref", "polynomial"} {
		if _, has := field[k]; has {
			clash = true
		}
	}
	if tm, isMap := field["time"].(map[string]any); isMap {
		if _, has := tm["parts"]; has {
			clash = true
		}
	}
	if ok && known && !clash {
		return nil
	}
	name := "None"
	if n, isText := field["name"].(string); isText {
		name = n
	}
	return fmt.Errorf("Field '%s': source is one of %s, on a type: number field that computes nothing else (PS-635)",
		name, strings.Join(frameSources, ", "))
}

// frameSource is the integer `source` names on decode (PS-635), or omitted with PS-636's
// warning where no FPort was given.
func (ctx *DecodeContext) frameSource(field Field) any {
	switch field.Source {
	case "$remaining":
		n := ctx.Remaining()
		if n < 0 {
			n = 0
		}
		return float64(n)
	case "$payloadLength":
		return float64(ctx.frameLength)
	}
	if !ctx.frameHasPort {
		ctx.Warnings = append(ctx.Warnings,
			fmt.Sprintf("Field '%s': no FPort was given, so it is omitted (PS-636)", field.Name))
		return omitted
	}
	return float64(ctx.frameFPort)
}

// frameSourceValue is what a `source` field reports for raw, through its modifiers and
// lookup, on the encode side; false where it reports nothing.
func frameSourceValue(field Field, raw int, endian string) (any, bool) {
	scratch := NewDecodeContext(nil, endian)
	v, err := applyLookupAndModifiers(float64(raw), field, scratch)
	if err != nil || v == omitted || v == nil {
		return nil, false
	}
	return v, true
}

// frameSourceMismatch is PS-637's rejection.
func frameSourceMismatch(field Field, given, found any, ok bool) error {
	gives := "no FPort"
	if ok {
		gives = echoText(found)
	}
	return fmt.Errorf("Field '%s' is given %s, but the frame gives %s (PS-637)",
		field.Name, echoText(given), gives)
}

// frameSourceAgrees compares a given value with the frame's.
func frameSourceAgrees(given, found any) bool {
	return reflect.DeepEqual(echoNormal(found), echoNormal(given))
}

// recordFrameSource queues a `source` field's given value (PS-637): `$remaining` as an
// echo reaching the end of the frame it closes with, the others for the finished payload.
func (ctx *EncodeContext) recordFrameSource(field Field, data map[string]any) {
	given, ok := data[field.Name]
	if !ok || given == nil {
		return
	}
	if field.Source == "$remaining" {
		ctx.echoes = append(ctx.echoes, echoRecord{len(ctx.Buffer), field, given, -1, ctx.Endian})
		return
	}
	if ctx.frameSources == nil {
		ctx.frameSources = &[]frameSourceCheck{}
	}
	*ctx.frameSources = append(*ctx.frameSources, frameSourceCheck{field, given})
}

// checkFrameSources checks each `$payloadLength` and `$fPort` value against the finished
// payload and the FPort the encode reports (0: none). The encoder neither pads the frame
// nor chooses the FPort to agree.
func (ctx *EncodeContext) checkFrameSources() error {
	if ctx.frameSources == nil {
		return nil
	}
	for _, c := range *ctx.frameSources {
		var found any
		ok := false
		if c.field.Source == "$payloadLength" {
			found, ok = frameSourceValue(c.field, len(ctx.Buffer), ctx.Endian)
		} else if ctx.FPort != 0 {
			found, ok = frameSourceValue(c.field, ctx.FPort, ctx.Endian)
		}
		if !ok || !frameSourceAgrees(c.given, found) {
			return frameSourceMismatch(c.field, c.given, found, ok)
		}
	}
	return nil
}
