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
     * PS-593, PS-619: the number of text bytes before the field's terminator, and the number
     * of terminator bytes the field consumes (0 with {@code consume_terminator: false}, or
     * where the end of the data ended the text). A payload that ends before one is an
     * underflow, which the field names (PS-535), unless the field declares
     * {@code end_terminates: true}; the region ends at a reserve or a tlv entry's end.
     */
    static int[] terminatedSpan(Field field, DecodeContext ctx) {
        byte[] data = ctx.getData();
        int end = ctx.getOffset() + ctx.remaining();
        for (int i = ctx.getOffset(); i < end; i++) {
            if ((data[i] & 0xFF) == field.getTerminator()) {
                return new int[] {i - ctx.getOffset(), field.isKeepTerminator() ? 0 : 1};
            }
        }
        if (field.isEndTerminates()) return new int[] {Math.max(ctx.remaining(), 0), 0};
        throw new SchemaException.BufferUnderflow(String.format(
                "no terminator byte 0x%02X before the end of the payload (PS-593)", field.getTerminator()));
    }

    private static String pyRepr(Object v) {
        if (v instanceof String s) return "'" + s + "'";
        if (v == null) return "None";
        if (v instanceof Boolean b) return b ? "True" : "False";
        return String.valueOf(v);
    }

    /**
     * PS-624 (CR-2026-190): the index of the first byte of the first sequence that is not
     * well-formed UTF-8 (RFC 3629), or -1 where every byte is; the position the reference's
     * UnicodeDecodeError.start gives.
     */
    static int firstBadUtf8(byte[] b) {
        int i = 0;
        while (i < b.length) {
            int c = b[i] & 0xFF;
            if (c < 0x80) { i++; continue; }
            int len;
            int lo = 0x80;
            int hi = 0xBF;
            if (c >= 0xC2 && c <= 0xDF) len = 1;
            else if (c >= 0xE0 && c <= 0xEF) { len = 2; if (c == 0xE0) lo = 0xA0; if (c == 0xED) hi = 0x9F; }
            else if (c >= 0xF0 && c <= 0xF4) { len = 3; if (c == 0xF0) lo = 0x90; if (c == 0xF4) hi = 0x8F; }
            else return i;
            for (int k = 1; k <= len; k++) {
                if (i + k >= b.length) return i;
                int d = b[i + k] & 0xFF;
                if (d < (k == 1 ? lo : 0x80) || d > (k == 1 ? hi : 0xBF)) return i;
            }
            i += len + 1;
        }
        return -1;
    }

    /**
     * PS-624: the bytes the byte rules left ({@code kept}, one ISO 8859-1 character per
     * byte) decoded as UTF-8; a sequence that is not well formed is an error naming the field
     * and the position of its first byte within the text.
     */
    static String decodeUtf8(String name, String kept) {
        byte[] raw = kept.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
        int bad = firstBadUtf8(raw);
        if (bad >= 0) {
            throw new SchemaException.DecodeException("Field '" + name + "': the bytes are not "
                    + "well-formed UTF-8 from byte " + bad + " of the text (PS-624)");
        }
        return new String(raw, java.nio.charset.StandardCharsets.UTF_8);
    }

    /** PS-624: {@code charset} is {@code utf-8}, on an ascii field; the prototype's text. */
    static void checkCharset(Map<?, ?> fm) {
        if (!fm.containsKey("type") || !fm.containsKey("charset")) return;
        if (!"ascii".equals(fm.get("type")) || !"utf-8".equals(fm.get("charset"))) {
            Object name = fm.get("name");
            throw new SchemaException("Field '" + (name instanceof String s ? s : "None")
                    + "': charset is utf-8, on an ascii field (PS-624)");
        }
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
        // PS-619 (CR-2026-188): both keys need a terminator and are true or false.
        if (fm.containsKey("consume_terminator") || fm.containsKey("end_terminates")) {
            if (!hasTerm) {
                throw new SchemaException("Field '" + shown + "': consume_terminator and "
                        + "end_terminates belong to a field with a terminator (PS-619)");
            }
            for (String key : new String[] {"consume_terminator", "end_terminates"}) {
                if (fm.containsKey(key) && !(fm.get(key) instanceof Boolean)) {
                    throw new SchemaException("Field '" + shown + "': " + key
                            + " is true or false (PS-619)");
                }
            }
        }
        if (isAscii && !fm.containsKey("length") && !hasTerm) {
            throw new SchemaException("Field '" + shown + "': ascii type requires 'length' field, "
                    + "or 'terminator' (PS-072)");
        }
    }
}
