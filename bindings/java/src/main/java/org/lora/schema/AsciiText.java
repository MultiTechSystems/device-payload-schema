package org.lora.schema;

import java.util.Map;

/**
 * CR-2026-177 (PS-534, PS-072, PS-593 to PS-595): an {@code ascii} field's {@code nul} mode
 * (trailing, stop, drop) and a {@code terminator} byte in place of {@code length}. Mirrors
 * apply_nul_mode, terminated_span and ascii_text_errors in tools/schema_interpreter.py.
 */
final class AsciiText {
    private AsciiText() {}

    /** PS-534: trailing removes trailing 0x00 bytes, stop ends the text at the first, drop removes all. */
    static String applyNulMode(String text, String mode) {
        if ("stop".equals(mode)) {
            int i = text.indexOf('\0');
            return i < 0 ? text : text.substring(0, i);
        }
        if ("drop".equals(mode)) return text.replace("\0", "");
        int end = text.length();
        while (end > 0 && text.charAt(end - 1) == '\0') end--;
        return text.substring(0, end);
    }

    /** A terminator that is a whole number, or null. */
    static Integer terminatorOf(Object v) {
        if (v instanceof Boolean) return null;
        if (v instanceof Number n && n.doubleValue() == Math.rint(n.doubleValue())) {
            return (int) n.doubleValue();
        }
        return null;
    }

    /**
     * PS-593: the number of text bytes before the field's terminator. A payload that ends
     * before one is an underflow, which the field names (PS-535).
     */
    static int terminatedSpan(Field field, DecodeContext ctx) {
        byte[] data = ctx.getData();
        int end = ctx.getOffset() + ctx.remaining();
        for (int i = ctx.getOffset(); i < end; i++) {
            if ((data[i] & 0xFF) == field.getTerminator()) return i - ctx.getOffset();
        }
        throw new SchemaException.BufferUnderflow(String.format(
                "no terminator byte 0x%02X before the end of the payload (PS-593)", field.getTerminator()));
    }

    private static String pyRepr(Object v) {
        if (v instanceof String s) return "'" + s + "'";
        if (v == null) return "None";
        if (v instanceof Boolean b) return b ? "True" : "False";
        return String.valueOf(v);
    }

    /** The load rule over one field mapping, in the spec prototype's text. */
    static void check(Map<?, ?> fm) {
        if (!fm.containsKey("type")) return;
        Object name = fm.get("name");
        String shown = name instanceof String s ? s : "None";
        Object type = fm.get("type");
        boolean isAscii = "ascii".equals(String.valueOf(type).toLowerCase());
        boolean hasTerm = fm.containsKey("terminator");
        if (fm.containsKey("nul") || hasTerm) {
            if (!isAscii) {
                throw new SchemaException("Field '" + shown + "': nul and terminator are defined "
                        + "only on an ascii field, not on " + type + " (PS-534, PS-593)");
            }
            if (fm.containsKey("nul")) {
                Object nul = fm.get("nul");
                if (!("trailing".equals(nul) || "stop".equals(nul) || "drop".equals(nul))) {
                    throw new SchemaException("Field '" + shown + "': nul must be trailing, stop or "
                            + "drop, not " + pyRepr(nul) + " (PS-534)");
                }
            }
            if (hasTerm) {
                Object term = fm.get("terminator");
                Integer n = terminatorOf(term);
                if (n == null || n < 0 || n > 255) {
                    throw new SchemaException("Field '" + shown + "': terminator must be a byte "
                            + "value 0 to 255, not " + pyRepr(term) + " (PS-593)");
                }
                if (fm.containsKey("length")) {
                    throw new SchemaException("Field '" + shown + "': an ascii field declares "
                            + "length or terminator, not both (PS-593)");
                }
            }
        }
        if (isAscii && !fm.containsKey("length") && !hasTerm) {
            throw new SchemaException("Field '" + shown + "': ascii type requires 'length' field, "
                    + "or 'terminator' (PS-072)");
        }
    }
}
