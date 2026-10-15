package org.lora.schema;

import java.util.Map;
import java.util.regex.Pattern;

/**
 * CR-2026-150 (PS-560, PS-561): an {@code ascii} field with {@code numeral: decimal|hex}
 * reports its digit text as an integer, strictly, and writes it back canonically. Mirrors
 * decode_numeral, encode_numeral and numeral_errors in tools/schema_interpreter.py.
 */
final class Numeral {
    private Numeral() {}

    private static final Pattern DECIMAL = Pattern.compile("[+-]?[0-9]+");
    private static final Pattern HEX = Pattern.compile("[0-9A-Fa-f]+");

    private static Pattern pattern(String numeral) {
        return "decimal".equals(numeral) ? DECIMAL : "hex".equals(numeral) ? HEX : null;
    }

    /** The integer the whole text spells, or an error naming the field; never a prefix of it. */
    static long decode(String name, String text, String numeral) {
        boolean ok = pattern(numeral).matcher(text).matches();
        long value = 0;
        if (ok) {
            try {
                value = Long.parseLong(text.startsWith("+") ? text.substring(1) : text,
                        "hex".equals(numeral) ? 16 : 10);
            } catch (NumberFormatException e) {
                ok = false;
            }
        }
        if (!ok) {
            throw new SchemaException.DecodeException("Field '" + name + "': '" + text
                    + "' is not a " + numeral + " numeral (PS-560)");
        }
        return value;
    }

    /**
     * The digits of the value in the declared base, uppercase hex, filled with 0 to the
     * length after any sign. A negative hex value, or one that does not fit, is a PS-168
     * error naming the field. {@code fixed} is null where the length is not fixed.
     */
    static String encode(String name, long n, Integer fixed, String numeral) {
        String digits = "hex".equals(numeral)
                ? Long.toHexString(Math.abs(n)).toUpperCase() : Long.toString(Math.abs(n));
        String sign = n < 0 ? "-" : "";
        int width = fixed != null ? fixed : sign.length() + digits.length();
        if (fixed == null && n < 0 && "hex".equals(numeral)) {          // PS-168, PS-595
            throw new SchemaException.FieldEncodeException("Field '" + name + "': " + n
                    + " is negative, which a hex numeral cannot write (PS-168)");
        }
        if ((n < 0 && "hex".equals(numeral)) || sign.length() + digits.length() > width) {
            throw new SchemaException.FieldEncodeException("Field '" + name + "': " + n
                    + " does not fit " + (fixed != null ? fixed : digits.length()) + " "
                    + numeral + " digits (PS-168)");
        }
        return sign + "0".repeat(width - sign.length() - digits.length()) + digits;
    }

    /** PS-560's load rule over one field mapping, in the spec prototype's text. */
    static void check(Map<?, ?> fm) {
        if (!fm.containsKey("numeral") || !fm.containsKey("type")) return;
        Object name = fm.get("name");
        String shownName = name instanceof String s ? s : "None";
        Object type = fm.get("type");
        if (!"ascii".equals(String.valueOf(type).toLowerCase())) {
            throw new SchemaException("Field '" + shownName + "': numeral is defined only on an "
                    + "ascii field, not on " + type + " (PS-560)");
        }
        Object numeral = fm.get("numeral");
        if (!(numeral instanceof String s) || pattern(s) == null) {
            throw new SchemaException("Field '" + shownName + "': numeral must be decimal or hex, "
                    + "not " + (numeral instanceof String t ? "'" + t + "'" : String.valueOf(numeral))
                    + " (PS-560)");
        }
    }
}
