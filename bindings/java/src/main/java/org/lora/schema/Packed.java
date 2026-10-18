package org.lora.schema;

import java.util.List;
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

    /**
     * The members of packed records seen so far, by identity: a field of a packed record
     * declares {@code packed_bits} as a part, not as a repeat (PS-639). The repeat is checked
     * before its members are parsed, so it registers them here and each is taken out when
     * it is checked.
     */
    private static final java.util.Set<Map<?, ?>> PARTS =
            java.util.Collections.synchronizedSet(java.util.Collections.newSetFromMap(new java.util.IdentityHashMap<>()));

    /** A packed repeat: {@code packed_bits} on the repeat (PS-621) or on a field of its record (PS-639). */
    static boolean isPacked(Map<?, ?> fm) {
        if (!"repeat".equals(fm.get("type"))) return false;
        if (fm.containsKey("packed_bits")) return true;
        if (fm.get("fields") instanceof List<?> members) {
            for (Object m : members) if (m instanceof Map<?, ?> mm && mm.containsKey("packed_bits")) return true;
        }
        return false;
    }

    /**
     * PS-621, PS-639 and PS-640 over one field mapping, in the spec prototype's text
     * ({@code check_packed_repeats}), the first rule broken named; packed_repeat_error in
     * tools/schema_interpreter.py.
     */
    static void check(Map<?, ?> fm) {
        boolean inRecord = PARTS.remove(fm);
        if (!fm.containsKey("type")) return;
        if (!((fm.containsKey("packed_bits") && !inRecord) || fm.containsKey("bit_offset") || isPacked(fm))) {
            return;
        }
        String name = fm.get("name") instanceof String s ? s : "None";
        String where = "Repeat '" + name + "'";
        if (!"repeat".equals(fm.get("type")) || !isPacked(fm)) {
            String hint = "bool".equals(fm.get("type")) ? "; did you mean `bit`?" : "";
            throw new SchemaException("Field '" + name + "': packed_bits and bit_offset belong to "
                    + "a packed repeat (PS-621, PS-639)" + hint);
        }
        if (fm.containsKey("packed_bits") && fm.containsKey("fields")) {
            throw new SchemaException(where + ": a packed record declares packed_bits on each field, "
                    + "not on the repeat (PS-639)");
        }
        if (fm.containsKey("bit_offset")) {
            Object offset = fm.get("bit_offset");
            Integer n = strictInt(offset);
            boolean ok = (n != null && n >= 0)
                    || (offset instanceof String s && s.startsWith("$") && s.length() > 1);
            if (!ok) {
                throw new SchemaException(where + ": bit_offset is a bit count, 0 or more, or a $ reference (PS-621)");
            }
        }
        if (fm.containsKey("consume")) {
            Object c = fm.get("consume");
            boolean zero = (c instanceof Number num && num.doubleValue() == 0) || Boolean.FALSE.equals(c);
            if (!zero) {
                throw new SchemaException(where + ": a packed repeat declares consume: 0 or no consume (PS-640)");
            }
        }
        List<Object[]> parts = new java.util.ArrayList<>();
        if (fm.containsKey("packed_bits")) {
            parts.add(new Object[] {fm.get("packed_bits"), fm.get("element")});
        } else {
            List<?> members = (List<?>) fm.get("fields");
            for (Object m : members) {
                if (!(m instanceof Map<?, ?> mm) || !mm.containsKey("packed_bits")) {
                    throw new SchemaException(where + ": every field of a packed record declares packed_bits (PS-639)");
                }
            }
            for (Object m : members) parts.add(new Object[] {((Map<?, ?>) m).get("packed_bits"), m});
        }
        for (Object[] part : parts) {
            Integer bits = strictInt(part[0]);
            if (bits == null || bits < 1 || bits > 32) {
                throw new SchemaException(where + ": packed_bits is an integer from 1 to 32 (PS-621)");
            }
            int[] width = part[1] instanceof Map<?, ?> e ? width(e) : null;
            if (width == null || width[0] < bits) {
                throw new SchemaException(where + ": a packed part is an integer type at least " + bits
                        + " bits wide (PS-621, PS-639)");
            }
        }
        if (!fm.containsKey("count") && !fm.containsKey("byte_length")) {
            throw new SchemaException(where + ": a packed repeat declares count or byte_length (PS-621)");
        }
        if (!fm.containsKey("packed_bits")) {
            for (Object m : (List<?>) fm.get("fields")) PARTS.add((Map<?, ?>) m);
        }
    }

    /** The value a referenced bit_offset holds, as PS-621's message writes it (JS String(n)). */
    static String shown(Object value) {
        if (value instanceof Number n && !(value instanceof Boolean)) return Encoder.jsNumber(n);
        if (value instanceof String s) return "'" + s + "'";
        if (value instanceof Boolean b) return b ? "True" : "False";
        return String.valueOf(value);
    }

    /** PS-621 (CR-2026-198): a referenced bit_offset that is not a bit count. */
    static String offsetError(String name, String offset, Object value) {
        return "Repeat '" + name + "': bit_offset " + offset + " is " + shown(value) + ", not a bit count (PS-621)";
    }

    /** The bit count a referenced offset value holds, else -1. */
    static long offsetValue(Object value) {
        if (!(value instanceof Number n) || value instanceof Boolean) return -1;
        double d = n.doubleValue();
        if (d != Math.floor(d) || d < 0 || Double.isInfinite(d)) return -1;
        return (long) d;
    }
}
