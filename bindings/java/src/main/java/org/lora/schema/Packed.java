package org.lora.schema;

import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * CR-2026-189 (PS-621 to PS-623): a repeat of single values whose elements are packed end
 * to end in {@code packed_bits} bits, most significant bit first, from {@code bit_offset}
 * bits into the byte at the read position. Mirrors packed_element_width and
 * packed_repeat_error in tools/schema_interpreter.py; the decode is Schema.decodePacked and
 * the encode Encoder.encodePacked.
 */
final class Packed {
    private Packed() {}

    private static final Pattern INTEGER = Pattern.compile("(u|s|uint|int|i)(8|16|24|32|64)");

    /** {width in bits, 1 if signed} of a packed element's integer type, else null. */
    static int[] width(Map<?, ?> element) {
        if (element == null || !(element.get("type") instanceof String t)) return null;
        Matcher m = INTEGER.matcher(t);
        if (!m.matches()) return null;
        boolean signed = !"u".equals(m.group(1)) && !"uint".equals(m.group(1));
        return new int[] {Integer.parseInt(m.group(2)), signed ? 1 : 0};
    }

    private static Integer strictInt(Object v) {
        if (v instanceof Integer || v instanceof Long) return ((Number) v).intValue();
        return null;
    }

    /** PS-621's load rule over one field mapping, in the spec prototype's text. */
    static void check(Map<?, ?> fm) {
        if (!fm.containsKey("type") || (!fm.containsKey("packed_bits") && !fm.containsKey("bit_offset"))) {
            return;
        }
        String name = fm.get("name") instanceof String s ? s : "None";
        String where = "Repeat '" + name + "'";
        if (!"repeat".equals(fm.get("type")) || !fm.containsKey("packed_bits")) {
            String hint = "bool".equals(fm.get("type")) ? "; did you mean `bit`?" : "";
            throw new SchemaException("Field '" + name + "': packed_bits and bit_offset belong to "
                    + "a repeat with `element` (PS-621)" + hint);
        }
        Integer bits = strictInt(fm.get("packed_bits"));
        if (bits == null || bits < 1 || bits > 32) {
            throw new SchemaException(where + ": packed_bits is an integer from 1 to 32 (PS-621)");
        }
        if (fm.containsKey("bit_offset")) {
            Integer offset = strictInt(fm.get("bit_offset"));
            if (offset == null || offset < 0 || offset > 7) {
                throw new SchemaException(where + ": bit_offset is an integer from 0 to 7 (PS-621)");
            }
        }
        int[] width = !fm.containsKey("fields") && fm.get("element") instanceof Map<?, ?> e ? width(e) : null;
        if (width == null || width[0] < bits) {
            throw new SchemaException(where + ": a packed element is one field of an integer type "
                    + "at least " + bits + " bits wide (PS-621)");
        }
        if (!fm.containsKey("count") && !fm.containsKey("byte_length")) {
            throw new SchemaException(where + ": a packed repeat declares count or byte_length (PS-621)");
        }
    }
}
