// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
)

// CR-2026-193 (PS-630, PS-631): a tlv entry with a length bounds its case. A case field
// reading past the entry's value bytes is an error naming the entry's tag and frame
// offset; value bytes the case leaves unread are skipped with a warning, and the next
// entry begins after them. The texts are the spec prototype's.

// tlvEntryWhere identifies a tlv entry as the prototype does: its tag (a one-part tag a
// decimal integer, a composite tag a list) and its offset in the frame.
func tlvEntryWhere(tag []int, start int) string {
	text := ""
	if len(tag) == 1 {
		text = strconv.Itoa(tag[0])
	} else {
		parts := make([]string, len(tag))
		for i, p := range tag {
			parts[i] = strconv.Itoa(p)
		}
		text = "[" + strings.Join(parts, ", ") + "]"
	}
	return fmt.Sprintf("tlv entry tag %s at offset %d", text, start)
}

// tlvEntryOverrun is PS-630's error for a short read inside an entry, or err unchanged.
func tlvEntryOverrun(err error, where string, length int) error {
	var under *BufferUnderflowError
	if !errors.As(err, &under) {
		return err
	}
	return fmt.Errorf("%s: %s; the case reads past the entry's %d value byte(s) (PS-630)",
		where, err.Error(), length)
}

// tlvEntryUnread is PS-631's warning for value bytes a case left unread.
func tlvEntryUnread(where string, count int) string {
	return fmt.Sprintf("%s: %d byte(s) of its value not read (PS-631)", where, count)
}
