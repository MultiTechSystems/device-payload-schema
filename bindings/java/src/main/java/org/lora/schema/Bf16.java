package org.lora.schema;

/**
 * CR-2026-149 (PS-558, PS-559): {@code bf16}, the upper half of an IEEE 754 binary32.
 * Mirrors decode_bf16 and encode_bf16 in tools/schema_interpreter.py.
 */
final class Bf16 {
    private Bf16() {}

    /** The largest finite bf16, 0x7F7F: (2 - 2^-7) * 2^127. */
    static final double MAX = (2 - 1.0 / 128) * Math.scalb(1.0, 127);

    /** The binary32 whose upper half is the word and whose lower half is zero. */
    static double decode(long word) {
        return Float.intBitsToFloat((int) (word & 0xFFFF) << 16);
    }

    /**
     * The nearest bf16 to the binary64 value, a tie to the even fraction, rounded once and
     * never through a binary32. A finite value that rounds past the largest bf16 is a PS-168
     * error naming the field; NaN and the infinities are their words.
     */
    static long encode(double value, String name) {
        if (Double.isNaN(value)) return 0x7FC0;
        long sign = (Double.doubleToRawLongBits(value) < 0) ? 0x8000 : 0;
        double magnitude = Math.abs(value);
        if (Double.isInfinite(magnitude)) return sign | 0x7F80;
        long word;
        int exponent = magnitude == 0 ? 0 : Math.getExponent(magnitude);   // magnitude = 1.f * 2^exponent
        if (magnitude == 0 || exponent < -126) {
            word = (long) Math.rint(Math.scalb(magnitude, 133));            // fraction * 2^-133
        } else {
            double scaled = Math.scalb(magnitude, -exponent) * 128;         // 128..256, exact
            long fraction = (long) Math.rint(scaled);
            if (fraction == 256) {
                fraction = 128;
                exponent += 1;
            }
            word = ((long) (exponent + 127) << 7) | (fraction - 128);
        }
        if (word >= 0x7F80) {
            throw new SchemaException.EncodeException("Field '" + name + "': "
                    + Encoder.jsNumber(value) + " is outside the range of bf16, "
                    + Encoder.jsNumber(-MAX) + " to " + Encoder.jsNumber(MAX) + " (PS-168)");
        }
        return word == 0 ? 0 : sign | word;   // CR-2026-157: a negative value rounding to zero is 0
    }
}
