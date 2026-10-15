package org.lora.schema;

import java.math.BigInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * CR-2026-170 (PS-430, PS-582, PS-096, PS-583): a {@code bitfield_string} part format is
 * {@code decimal}, {@code sdecimal}, {@code hex} or {@code hex:upper}, optionally followed by
 * {@code :N}, a minimum width from 1 to 32 that never cuts. {@code sdecimal} reads its bits as
 * two's complement; an encoder rejects a segment the part's width cannot hold. Mirrors
 * parse_part_format, format_part_number, format_bitfield_part and parse_bitfield_segment in
 * tools/schema_interpreter.py. {@link #formatNumber} is the one formatter, for CR-2026-176's
 * templates too.
 */
final class BitfieldParts {
    private BitfieldParts() {}

    private static final Pattern FORMAT =
            Pattern.compile("(decimal|sdecimal|hex:upper|hex)(?::([1-9][0-9]?))?");
    private static final Pattern DECIMAL = Pattern.compile("[0-9]+");
    private static final Pattern SDECIMAL = Pattern.compile("-?[0-9]+");
    private static final Pattern HEX = Pattern.compile("[0-9A-Fa-f]+");

    /** A part format's base and its minimum width, 0 where it declares none. */
    record Format(String base, int width) {}

    /** The part format, or PS-430's rejection in the spec prototype's text. */
    static Format parse(Object format) {
        if (format instanceof String text) {
            Matcher m = FORMAT.matcher(text);
            if (m.matches()) {
                int width = m.group(2) == null ? 0 : Integer.parseInt(m.group(2));
                if (width <= 32) return new Format(m.group(1), width);
            }
        }
        throw new SchemaException("bitfield_string part format must be decimal, sdecimal, hex or "
                + "hex:upper, with an optional :N width from 1 to 32, not " + Meta.pyRepr(format)
                + " (PS-430)");
    }

    /**
     * A magnitude in a part format's base - hex lower or upper case, else decimal - with a
     * leading {@code -} where negative, zero-filled on the left to {@code width} digits and never
     * cut (PS-430, PS-582).
     */
    static String formatNumber(boolean negative, BigInteger magnitude, String base, int width) {
        String digits = base.startsWith("hex") ? magnitude.toString(16) : magnitude.toString();
        if ("hex:upper".equals(base)) digits = digits.toUpperCase();
        StringBuilder out = new StringBuilder(negative ? "-" : "");
        for (int i = digits.length(); i < width; i++) out.append('0');
        return out.append(digits).toString();
    }

    /** One part from its unsigned bits; sdecimal is two's complement of the width (PS-582). */
    static String format(long raw, int bits, Object format) {
        Format f = parse(format);
        BigInteger value = new BigInteger(Long.toUnsignedString(raw));
        if ("sdecimal".equals(f.base()) && bits > 0 && value.testBit(bits - 1)) {
            return formatNumber(true, BigInteger.ONE.shiftLeft(bits).subtract(value), f.base(), f.width());
        }
        return formatNumber(false, value, f.base(), f.width());
    }

    /**
     * One segment's unsigned bits (PS-096, PS-431, PS-583): leading zeros are accepted, hex
     * without regard to case, a {@code -} only on sdecimal; a value the part's width cannot
     * hold is rejected naming the field and the segment, never masked.
     */
    static long parseSegment(String segment, int bits, Object format, String name) {
        String base = parse(format).base();
        String text = segment.strip();
        Pattern pattern = "sdecimal".equals(base) ? SDECIMAL : "decimal".equals(base) ? DECIMAL : HEX;
        if (!pattern.matcher(text).matches()) {
            throw new SchemaException.EncodeException("Field '" + name + "': segment "
                    + Meta.pyRepr(segment) + " is not a " + base + " part (PS-096)");
        }
        BigInteger number = new BigInteger(text, base.startsWith("hex") ? 16 : 10);
        BigInteger low = BigInteger.ZERO;
        BigInteger high = BigInteger.ONE.shiftLeft(bits).subtract(BigInteger.ONE);
        if ("sdecimal".equals(base)) {
            BigInteger half = BigInteger.ONE.shiftLeft(bits - 1);
            low = half.negate();
            high = half.subtract(BigInteger.ONE);
        }
        if (number.compareTo(low) < 0 || number.compareTo(high) > 0) {
            throw new SchemaException.EncodeException("Field '" + name + "': segment "
                    + Meta.pyRepr(segment) + " does not fit a " + bits + "-bit " + base + " part, "
                    + low + " to " + high + " (PS-583)");
        }
        if (number.signum() < 0) number = number.add(BigInteger.ONE.shiftLeft(bits));
        return number.longValue();
    }

    /**
     * CR-2026-183 (PS-096, PS-605, PS-606): a value's segments, one per part. The declared
     * prefix is required, the rest split on the delimiter, or by each part's {@code :N} width
     * where the delimiter is empty; a segment count other than the part count is rejected,
     * never filled or cut. Mirrors bitfield_segments and split_by_width.
     */
    static java.util.List<String> segments(String name, String prefix, String delimiter,
            java.util.List<java.util.List<Object>> parts, String value) {
        String text = value;
        if (prefix != null && !prefix.isEmpty()) {
            if (!text.startsWith(prefix)) {
                throw new SchemaException.EncodeException("Field '" + name + "': "
                        + Meta.pyRepr(value) + " does not begin with its prefix "
                        + Meta.pyRepr(prefix) + " (PS-605)");
            }
            text = text.substring(prefix.length());
        }
        java.util.List<String> segments;
        if ("".equals(delimiter)) {
            segments = splitByWidth(text, parts, name, value);
        } else {
            segments = java.util.Arrays.asList(
                    text.split(Pattern.quote(delimiter == null ? "." : delimiter), -1));
        }
        if (segments.size() != parts.size()) {
            throw new SchemaException.EncodeException("Field '" + name + "': " + Meta.pyRepr(value)
                    + " has " + segments.size() + " segment(s) for " + parts.size()
                    + " part(s) (PS-605)");
        }
        return segments;
    }

    /** PS-606: split by width; a part lacking a width for its largest value, or sdecimal,
     *  makes the field unencodable (it still decodes). */
    private static java.util.List<String> splitByWidth(String text,
            java.util.List<java.util.List<Object>> parts, String name, String value) {
        java.util.List<Integer> widths = new java.util.ArrayList<>();
        int total = 0;
        for (java.util.List<Object> part : parts) {
            int bits = ((Number) part.get(1)).intValue();
            Format f = parse(part.size() > 2 ? part.get(2) : "decimal");
            int largest = bits > 0
                    ? formatNumber(false, BigInteger.ONE.shiftLeft(bits).subtract(BigInteger.ONE),
                            f.base(), 0).length()
                    : 1;
            if ("sdecimal".equals(f.base()) || f.width() == 0 || largest > f.width()) {
                throw new SchemaException.EncodeException("Field '" + name + "': with an empty "
                        + "delimiter every part needs a :N width that holds its largest value, "
                        + "and none may be sdecimal, so this field cannot be encoded (PS-606)");
            }
            widths.add(f.width());
            total += f.width();
        }
        if (text.length() != total) {
            StringBuilder list = new StringBuilder("[");
            for (int i = 0; i < widths.size(); i++) list.append(i == 0 ? "" : ", ").append(widths.get(i));
            throw new SchemaException.EncodeException("Field '" + name + "': " + Meta.pyRepr(value)
                    + " has " + text.length() + " characters for parts of widths " + list
                    + "] (PS-606)");
        }
        java.util.List<String> out = new java.util.ArrayList<>();
        int at = 0;
        for (int w : widths) {
            out.add(text.substring(at, at + w));
            at += w;
        }
        return out;
    }
}
