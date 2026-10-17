package org.lora.schema;

import java.util.ArrayList;
import java.util.List;

/**
 * CR-2026-193 (PS-630, PS-631): a tlv entry with a length bounds its case. A case field
 * reading past the entry's value bytes is an error naming the entry's tag and frame offset;
 * value bytes the case leaves unread are skipped with a warning, and the next entry begins
 * after them. The texts are the spec prototype's.
 */
final class TlvEntryBound {
    private TlvEntryBound() {}

    /** The entry as the prototype names it: a one-part tag a decimal, a composite a list. */
    static String where(List<Integer> tag, int start) {
        String text;
        if (tag.size() == 1) {
            text = String.valueOf(tag.get(0));
        } else {
            List<String> parts = new ArrayList<>();
            for (Integer p : tag) parts.add(String.valueOf(p));
            text = "[" + String.join(", ", parts) + "]";
        }
        return "tlv entry tag " + text + " at offset " + start;
    }

    /** PS-630: a short read inside an entry. */
    static SchemaException.DecodeException overrun(String where, RuntimeException under,
                                                   int length) {
        return new SchemaException.DecodeException(where + ": " + under.getMessage()
                + "; the case reads past the entry's " + length + " value byte(s) (PS-630)");
    }

    /** PS-631: value bytes the case left unread. */
    static String unread(String where, int count) {
        return where + ": " + count + " byte(s) of its value not read (PS-631)";
    }
}
