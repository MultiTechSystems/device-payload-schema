package org.lora.schema;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A range case key, {@code "2..5"} or {@code "12.1..35.4"} (PS-398): two numbers, each a
 * decimal integer, a hexadecimal integer or a decimal with a fraction or exponent, either
 * of them negative. A bound is compared as the number the YAML spells, an integer staying
 * an integer, so a key such as {@code "12.1..35.4"} holds a computed 12.1 and 35.4 exactly.
 */
final class RangeKey {
    private static final String NUMBER =
            "[+-]?(?:0[xX][0-9a-fA-F]+|\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)";
    private static final Pattern KEY =
            Pattern.compile("^\\s*(" + NUMBER + ")\\s*\\.\\.\\s*(" + NUMBER + ")\\s*$");

    private final double lo;
    private final double hi;
    private final Long loInt;
    private final Long hiInt;

    private RangeKey(double lo, double hi, Long loInt, Long hiInt) {
        this.lo = lo;
        this.hi = hi;
        this.loInt = loInt;
        this.hiInt = hiInt;
    }

    /** The bounds of a range case key, or null where the text is not one. */
    static RangeKey parse(String text) {
        Matcher m = KEY.matcher(text);
        if (!m.matches()) return null;
        Long loInt = integer(m.group(1)), hiInt = integer(m.group(2));
        return new RangeKey(number(m.group(1), loInt), number(m.group(2), hiInt), loInt, hiInt);
    }

    private static Long integer(String text) {
        String digits = text.startsWith("+") ? text.substring(1) : text;
        boolean negative = digits.startsWith("-");
        if (negative) digits = digits.substring(1);
        int radix = 10;
        if (digits.length() > 2 && (digits.startsWith("0x") || digits.startsWith("0X"))) {
            digits = digits.substring(2);
            radix = 16;
        } else if (digits.contains(".") || digits.contains("e") || digits.contains("E")) {
            return null;
        }
        try {
            return Long.parseLong((negative ? "-" : "") + digits, radix);
        } catch (NumberFormatException tooLarge) {
            return null;
        }
    }

    private static double number(String text, Long integer) {
        return integer != null ? (double) integer : Double.parseDouble(text);
    }

    boolean contains(long value) {
        if (loInt != null && hiInt != null) return value >= loInt && value <= hiInt;
        return value >= lo && value <= hi;
    }

    boolean contains(double value) {
        return value >= lo && value <= hi;
    }

    /** Why a case key spelled like a range is not one (PS-398, PS-151), or null. */
    static String error(String key) {
        if (!key.contains("..") || key.trim().startsWith("[")) return null;
        RangeKey range = parse(key);
        if (range == null) {
            return "a range key is `start..end` with two numbers, each an integer or a "
                    + "decimal, such as 2..5 or 12.1..35.4 (PS-398)";
        }
        if (range.lo > range.hi) return "a range key has start <= end (PS-151, PS-398)";
        return null;
    }
}
