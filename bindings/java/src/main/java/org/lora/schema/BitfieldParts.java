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
}
