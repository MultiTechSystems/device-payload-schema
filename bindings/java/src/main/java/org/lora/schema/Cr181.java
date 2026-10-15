package org.lora.schema;

import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * CR-2026-181 (PS-081, PS-601, PS-602; PS-527 and PS-551 amended): an encoder writes a
 * {@code bytes}, {@code hex} or {@code base64} value exactly as given. A string is read in
 * the field's own format - base64, padded or not, for {@code format: base64} and the base64
 * type, otherwise hex in either case with the declared separator or without - and an array
 * gives integers 0 to 255. A malformed value, and one whose byte count is not a literal
 * {@code length}, is rejected naming the field, in the spec prototype's words. The encoder
 * used to cut and pad ({@code pad}), mask array elements with {@code & 0xFF}, read an
 * unparseable element as 0, strip ':' where the field declared no separator, and let
 * {@link java.util.Base64}'s errors through unnamed.
 */
final class Cr181 {
    private Cr181() {}

    private static final Pattern HEX = Pattern.compile("(?:[0-9A-Fa-f]{2})*");
    private static final Pattern BASE64 = Pattern.compile("[A-Za-z0-9+/]*={0,2}");
    private static final String ALPHABET =
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

    static boolean isByteString(FieldType t) {
        return t == FieldType.BYTES || t == FieldType.HEX || t == FieldType.BASE64;
    }

    private static SchemaException fail(String message) {
        return new SchemaException.FieldEncodeException(message);
    }

    /** byte_string_input: the bytes a value gives (PS-081), or PS-601's rejection. */
    static byte[] input(Field f, Object value) {
        String name = f.getName();
        if (value instanceof byte[] raw) return raw;
        if (value instanceof List<?> list) {
            byte[] out = new byte[list.size()];
            for (int i = 0; i < list.size(); i++) {
                Object item = list.get(i);
                double d = item instanceof Number n && !(item instanceof Boolean) ? n.doubleValue() : Double.NaN;
                if (Double.isNaN(d) || d != Math.rint(d) || d < 0 || d > 255) {
                    throw fail("Field '" + name + "': an array element is not an integer from 0 to 255 (PS-601)");
                }
                out[i] = (byte) (int) d;
            }
            return out;
        }
        if (!(value instanceof String text)) {
            throw fail("Field '" + name + "': a " + jsonType(value) + " is not a byte string value (PS-081)");
        }
        boolean base64 = f.getType() == FieldType.BASE64
                || (f.getType() == FieldType.BYTES && "base64".equals(f.getFormat()));
        if (base64) {
            String t = text.strip();
            String padded = t + "====".substring(0, (4 - t.length() % 4) % 4);
            String body = padded.replaceAll("=+$", "");
            if (!BASE64.matcher(padded).matches() || body.length() % 4 == 1) {
                throw fail("Field '" + name + "': '" + text + "' is not valid base64 (PS-601)");
            }
            // The alphabet is checked; trailing bits are ignored, as Python's b64decode does.
            byte[] out = new byte[body.length() * 6 / 8];
            int acc = 0, bits = 0, k = 0;
            for (int i = 0; i < body.length(); i++) {
                acc = ((acc << 6) | ALPHABET.indexOf(body.charAt(i))) & 0xFFFF;
                bits += 6;
                if (bits >= 8) {
                    bits -= 8;
                    out[k++] = (byte) (acc >> bits);
                }
            }
            return out;
        }
        String separator = f.getType() == FieldType.BYTES ? f.getSeparator() : null;
        boolean sep = separator != null && !separator.isEmpty();
        String hex = sep ? text.replace(separator, "") : text;
        if (!HEX.matcher(hex).matches()) {
            throw fail("Field '" + name + "': '" + text + "' is not well-formed hex"
                    + (sep ? " with separator '" + separator + "'" : "") + " (PS-601)");
        }
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }

    static String jsonType(Object value) {
        if (value == null) return "null";
        if (value instanceof Boolean) return "boolean";
        if (value instanceof Number) return "number";
        if (value instanceof Map<?, ?>) return "object";
        return value.getClass().getSimpleName();
    }

    /** A literal {@code length}, or null where the field declares {@code remaining}, a
     * field name or none. */
    static Integer literalLength(Field f) {
        if (f.getLengthRef() != null && !f.getLengthRef().isEmpty()) return null;
        if (!f.isLengthDeclared() || f.getLength() < 0) return null;
        return f.getLength();
    }

    /** PS-602 for a literal length: the value's byte count, never cut or padded. */
    static byte[] checkLength(Field f, byte[] raw) {
        Integer n = literalLength(f);
        if (n != null && raw.length != n) {
            throw fail("Field '" + f.getName() + "' is given " + raw.length + " bytes for a length of "
                    + n + " (PS-602)");
        }
        return raw;
    }

    static byte[] encode(Field f, Object value) {
        return checkLength(f, input(f, value));
    }

    /** PS-527 as amended: well formed, and of a literal length's byte count. */
    static boolean takes(Field f, Object value) {
        try {
            encode(f, value);
            return true;
        } catch (SchemaException e) {
            return false;
        }
    }

    /** A repeat takes a list whose elements its members take (the Tektelic orca shape). */
    static boolean[] repeatTakes(Field f, List<?> list) {
        boolean exact = true;
        List<Field> members = f.getFields();
        if (members == null) return new boolean[] {true, true};
        String element = f.getElementName();
        for (Object item : list) {
            if (item == null) continue;
            Map<?, ?> record = element != null ? Map.of(element, item)
                    : item instanceof Map<?, ?> m ? m : null;
            if (record == null) continue;
            for (Field member : members) {
                if (member.getName() == null || record.get(member.getName()) == null) continue;
                boolean[] took = Encoder.caseTakes(member, record.get(member.getName()));
                if (!took[0]) return new boolean[] {false, false};
                exact = exact && took[1];
            }
        }
        return new boolean[] {true, exact};
    }
}
