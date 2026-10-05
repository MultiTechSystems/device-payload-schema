package org.lora.schema;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;

/**
 * 0.5.2 wave 4: the word-ordered float and the fourth ordering (CR-2026-046, -047), the
 * MCCI minifloats (CR-2026-063) and the named encodings (CR-2026-064). Each method mirrors
 * the function of the same purpose in tools/schema_interpreter.py.
 */
final class Wave4 {
    private Wave4() {}

    static boolean isWordOrdered(FieldType t) {
        return t == FieldType.U32LE16 || t == FieldType.S32LE16 || t == FieldType.F32LE16
                || t == FieldType.U32BE16LE || t == FieldType.S32BE16LE || t == FieldType.F32BE16LE;
    }

    private static boolean le16(FieldType t) {
        return t == FieldType.U32LE16 || t == FieldType.S32LE16 || t == FieldType.F32LE16;
    }

    /** PS-271, PS-362, PS-363: two 16-bit units in an order the type fixes. */
    static Object readWordOrdered(FieldType t, byte[] d) {
        long word = le16(t)
                ? ((d[0] & 0xFFL) << 8 | (d[1] & 0xFFL)) | ((d[2] & 0xFFL) << 8 | (d[3] & 0xFFL)) << 16
                : ((d[1] & 0xFFL) << 8 | (d[0] & 0xFFL)) << 16 | ((d[3] & 0xFFL) << 8 | (d[2] & 0xFFL));
        if (t == FieldType.F32LE16 || t == FieldType.F32BE16LE) {
            return (double) Float.intBitsToFloat((int) word);
        }
        if ((t == FieldType.S32LE16 || t == FieldType.S32BE16LE) && word >= 0x80000000L) {
            return word - 0x100000000L;
        }
        return word;
    }

    static byte[] writeWordOrdered(FieldType t, double value) {
        long word = (t == FieldType.F32LE16 || t == FieldType.F32BE16LE)
                ? Float.floatToIntBits((float) value) & 0xFFFFFFFFL
                : Math.round(Math.rint(value)) & 0xFFFFFFFFL;
        int high = (int) (word >>> 16), low = (int) (word & 0xFFFF);
        if (le16(t)) {
            return new byte[]{(byte) (low >> 8), (byte) low, (byte) (high >> 8), (byte) high};
        }
        return new byte[]{(byte) high, (byte) (high >> 8), (byte) low, (byte) (low >> 8)};
    }

    static int minifloatSize(FieldType t) {
        return t == FieldType.SFLT24 ? 3 : 2;
    }

    /** PS-417 to PS-419. Null where there is no value (sflt24 exponent 127). Never IEEE half (PS-421). */
    static Double decodeMinifloat(FieldType t, long w) {
        double v;
        if (t == FieldType.UFLT16) {
            v = (w & 0x0FFF) / 4096.0 * Math.pow(2, (w >>> 12) - 15);
        } else if (t == FieldType.SFLT16) {
            v = (w & 0x07FF) / 2048.0 * Math.pow(2, ((w >>> 11) & 0x0F) - 15);
            if ((w & 0x8000) != 0) v = -v;
        } else {
            long e = (w >>> 16) & 0x7F, f = w & 0xFFFF;
            if (e == 127) return null;
            v = e == 0 ? f / 65536.0 * Math.pow(2, -62) : (1 + f / 65536.0) * Math.pow(2, e - 63);
            if ((w & 0x800000) != 0) v = -v;
        }
        return v == 0 ? 0.0 : v;   // -0 reported as 0 (PS-418)
    }

    private static long roundHalfEven(BigDecimal x) {
        return x.setScale(0, RoundingMode.HALF_EVEN).longValueExact();
    }

    private static BigDecimal pow2(int n) {
        return n >= 0 ? new BigDecimal(BigInteger.ONE.shiftLeft(n))
                : BigDecimal.ONE.divide(new BigDecimal(BigInteger.ONE.shiftLeft(-n)));
    }

    /** PS-420: the smallest exponent whose fraction fits, ties to even; out of range is an error. */
    static long encodeMinifloat(FieldType t, double value) {
        BigDecimal exact = new BigDecimal(value);
        boolean negative = exact.signum() < 0;
        BigDecimal magnitude = exact.abs();
        if (t == FieldType.UFLT16 || t == FieldType.SFLT16) {
            if (negative && t == FieldType.UFLT16) {
                throw new SchemaException.EncodeException(value + " is negative; uflt16 holds [0, 1) (PS-420)");
            }
            int bits = t == FieldType.SFLT16 ? 11 : 12;
            for (int e = 0; e < 16; e++) {
                long f = roundHalfEven(magnitude.multiply(pow2(bits + 15 - e)));
                if (f < (1L << bits)) {
                    long word = ((long) e << bits) | f;
                    return negative ? word | 0x8000 : word;
                }
            }
            throw new SchemaException.EncodeException(value + " is outside the range of " + t + " (PS-420)");
        }
        long sign = negative ? 0x800000 : 0;
        if (magnitude.signum() == 0) return sign;
        for (int e = 1; e < 127; e++) {
            if (magnitude.compareTo(pow2(e - 62)) < 0) {
                if (magnitude.compareTo(pow2(e - 63)) < 0) break;
                BigDecimal fraction = magnitude.divide(pow2(e - 63)).subtract(BigDecimal.ONE);
                long f = roundHalfEven(fraction.multiply(BigDecimal.valueOf(65536)));
                if (f == 65536) continue;
                return sign | ((long) e << 16) | f;
            }
        }
        if (magnitude.compareTo(pow2(-62)) >= 0) {
            throw new SchemaException.EncodeException(value + " is outside the range of sflt24 (PS-420)");
        }
        long f = roundHalfEven(magnitude.multiply(BigDecimal.valueOf(65536)).multiply(pow2(62)));
        return f >= 65536 ? sign | (1L << 16) : sign | f;
    }

    /** PS-422 to PS-425: a named code applied to the unsigned integer read. */
    static long decodeEncoding(long raw, String encoding, int size, String name) {
        int bits = size * 8;
        switch (encoding) {
            case "sign_magnitude": {
                long sign = 1L << (bits - 1);
                return (raw & sign) != 0 ? -(raw & (sign - 1)) : raw;
            }
            case "bcd": {
                long out = 0;
                for (int i = size * 2 - 1; i >= 0; i--) {
                    long digit = (raw >>> (4 * i)) & 0x0F;
                    if (digit > 9) {
                        throw new SchemaException.DecodeException("field '" + name
                                + "': invalid BCD digit " + digit + " (PS-424)");
                    }
                    out = out * 10 + digit;
                }
                return out;
            }
            case "gray": {
                long out = raw;
                for (int shift = 1; shift < bits; shift <<= 1) out ^= out >>> shift;
                return out;
            }
            default:
                return raw;
        }
    }

    /** The inverse, rejecting a value with no representation (PS-463). */
    static long encodeEncoding(long value, String encoding, int size, String name) {
        int bits = size * 8;
        switch (encoding) {
            case "sign_magnitude": {
                long sign = 1L << (bits - 1);
                if (Math.abs(value) > sign - 1) {
                    throw new SchemaException.EncodeException("field '" + name + "': " + value
                            + " has no " + bits + "-bit sign-magnitude form (PS-463)");
                }
                return value < 0 ? sign | -value : value;
            }
            case "bcd": {
                if (value < 0 || value > Math.pow(10, size * 2) - 1) {
                    throw new SchemaException.EncodeException("field '" + name + "': " + value
                            + " has no " + bits + "-bit BCD form (PS-463)");
                }
                long out = 0;
                for (int shift = 0; value > 0; shift += 4) {
                    out |= (value % 10) << shift;
                    value /= 10;
                }
                return out;
            }
            case "gray":
                return value ^ (value >>> 1);
            default:
                return value;
        }
    }
}
