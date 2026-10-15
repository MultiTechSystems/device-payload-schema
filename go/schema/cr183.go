// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"strconv"
	"strings"
)

// CR-2026-183 (PS-096, PS-605, PS-606): a bitfield_string value's segments on encode. The
// declared prefix is required, the segment count must equal the part count (PS-605), and
// an empty delimiter splits by each part's `:N` width (PS-606). Mirrors bitfield_segments
// and split_by_width in tools/schema_interpreter.py.

// bitfieldSegments removes the prefix and splits the rest, one segment per part.
func bitfieldSegments(field Field, parts [][]any, value string) ([]string, error) {
	text := value
	if field.Prefix != "" {
		if !strings.HasPrefix(text, field.Prefix) {
			return nil, fmt.Errorf("Field '%s': %s does not begin with its prefix %s (PS-605)",
				field.Name, pyRepr(value), pyRepr(field.Prefix))
		}
		text = text[len(field.Prefix):]
	}
	var segments []string
	if field.Delimiter == "" && field.DelimiterDeclared {
		var err error
		if segments, err = splitByWidth(text, parts, field.Name, value); err != nil {
			return nil, err
		}
	} else {
		delimiter := field.Delimiter
		if delimiter == "" {
			delimiter = "."
		}
		segments = strings.Split(text, delimiter)
	}
	if len(segments) != len(parts) {
		return nil, fmt.Errorf("Field '%s': %s has %d segment(s) for %d part(s) (PS-605)",
			field.Name, pyRepr(value), len(segments), len(parts))
	}
	return segments, nil
}

// splitByWidth splits by each part's `:N` width (PS-606). A part with no width holding its
// largest value, or an sdecimal part, makes the field unencodable; it still decodes.
func splitByWidth(text string, parts [][]any, name, value string) ([]string, error) {
	var widths []string
	var sizes []int
	total := 0
	for _, part := range parts {
		bits, _ := toInt(part[1])
		var format any = "decimal"
		if len(part) > 2 {
			format = part[2]
		}
		base, width, err := parsePartFormat(format)
		if err != nil {
			return nil, err
		}
		largest := 1
		if bits > 0 {
			largest = len(formatBitfieldPart(maxBits(bits), bits, base))
		}
		if base == "sdecimal" || width == 0 || largest > width {
			return nil, fmt.Errorf("Field '%s': with an empty delimiter every part needs a "+
				":N width that holds its largest value, and none may be sdecimal, so this "+
				"field cannot be encoded (PS-606)", name)
		}
		widths = append(widths, strconv.Itoa(width))
		sizes = append(sizes, width)
		total += width
	}
	if len(text) != total {
		return nil, fmt.Errorf("Field '%s': %s has %d characters for parts of widths [%s] (PS-606)",
			name, pyRepr(value), len(text), strings.Join(widths, ", "))
	}
	segments := make([]string, 0, len(sizes))
	at := 0
	for _, size := range sizes {
		segments = append(segments, text[at:at+size])
		at += size
	}
	return segments, nil
}

// maxBits is a part's largest unsigned value, 2^bits - 1.
func maxBits(bits int) uint64 {
	if bits >= 64 {
		return ^uint64(0)
	}
	return (uint64(1) << bits) - 1
}
