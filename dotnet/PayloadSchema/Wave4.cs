// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Numerics;

namespace PayloadSchema;

/// <summary>
/// 0.5.2 wave 4: the word-ordered float and the fourth ordering (CR-2026-046, -047), the
/// MCCI minifloats (CR-2026-063) and the named encodings (CR-2026-064). Each method mirrors
/// the function of the same purpose in tools/schema_interpreter.py.
/// </summary>
static class Wave4
{
    public static bool IsWordOrdered(FieldType t) => t is FieldType.U32LE16 or FieldType.S32LE16
        or FieldType.F32LE16 or FieldType.U32BE16LE or FieldType.S32BE16LE or FieldType.F32BE16LE;

    static bool Le16(FieldType t) => t is FieldType.U32LE16 or FieldType.S32LE16 or FieldType.F32LE16;

    /// <summary>PS-271, PS-362, PS-363: two 16-bit units in an order the type fixes.</summary>
    public static object ReadWordOrdered(FieldType t, ReadOnlySpan<byte> d)
    {
        uint word = Le16(t)
            ? (uint)(d[0] << 8 | d[1]) | (uint)(d[2] << 8 | d[3]) << 16
            : (uint)(d[1] << 8 | d[0]) << 16 | (uint)(d[3] << 8 | d[2]);
        if (t is FieldType.F32LE16 or FieldType.F32BE16LE)
            return (double)BitConverter.UInt32BitsToSingle(word);
        if (t is FieldType.S32LE16 or FieldType.S32BE16LE)
            return (long)(int)word;
        return (ulong)word;
    }

    public static byte[] WriteWordOrdered(FieldType t, double value)
    {
        uint word = t is FieldType.F32LE16 or FieldType.F32BE16LE
            ? BitConverter.SingleToUInt32Bits((float)value)
            : unchecked((uint)(long)Math.Round(value, MidpointRounding.ToEven));
        uint high = word >> 16, low = word & 0xFFFF;
        return Le16(t)
            ? new[] { (byte)(low >> 8), (byte)low, (byte)(high >> 8), (byte)high }
            : new[] { (byte)high, (byte)(high >> 8), (byte)low, (byte)(low >> 8) };
    }

    public static int MinifloatSize(FieldType t) => t == FieldType.SFlt24 ? 3 : 2;

    /// <summary>PS-417 to PS-419; null where there is no value. Never IEEE half (PS-421).</summary>
    public static double? DecodeMinifloat(FieldType t, ulong w)
    {
        double v;
        if (t == FieldType.UFlt16)
            v = (w & 0x0FFF) / 4096.0 * Math.Pow(2, (double)(w >> 12) - 15);
        else if (t == FieldType.SFlt16)
        {
            v = (w & 0x07FF) / 2048.0 * Math.Pow(2, (double)((w >> 11) & 0x0F) - 15);
            if ((w & 0x8000) != 0) v = -v;
        }
        else
        {
            ulong e = (w >> 16) & 0x7F, f = w & 0xFFFF;
            if (e == 127) return null;
            v = e == 0 ? f / 65536.0 * Math.Pow(2, -62) : (1 + f / 65536.0) * Math.Pow(2, (double)e - 63);
            if ((w & 0x800000) != 0) v = -v;
        }
        return v == 0 ? 0.0 : v;   // -0 reported as 0 (PS-418)
    }

    /// <summary>An exact rational: numerator over a power of two.</summary>
    readonly record struct Exact(BigInteger Num, int Shift)
    {
        public static Exact Of(double value)
        {
            long bits = BitConverter.DoubleToInt64Bits(Math.Abs(value));
            int exp = (int)((bits >> 52) & 0x7FF);
            long mant = bits & 0xFFFFFFFFFFFFFL;
            if (exp == 0) return new Exact(mant, 1074);
            return new Exact(mant | (1L << 52), 1075 - exp);
        }
    }

    /// <summary>round(x * 2^k) ties to even, for x = num / 2^shift.</summary>
    static BigInteger RoundScaled(Exact x, int k)
    {
        int s = x.Shift - k;
        if (s <= 0) return x.Num << -s;
        BigInteger q = x.Num >> s, rem = x.Num - (q << s), half = BigInteger.One << (s - 1);
        if (rem > half || (rem == half && !q.IsEven)) q += 1;
        return q;
    }

    /// <summary>Compare x with 2^n.</summary>
    static int ComparePow2(Exact x, int n)
    {
        int s = x.Shift + n;
        return s >= 0 ? x.Num.CompareTo(BigInteger.One << s) : (x.Num << -s).CompareTo(BigInteger.One);
    }

    /// <summary>PS-420: the smallest exponent whose fraction fits, ties to even; out of range is an error.</summary>
    public static ulong EncodeMinifloat(FieldType t, double value)
    {
        bool negative = value < 0;
        var m = Exact.Of(value);
        if (t is FieldType.UFlt16 or FieldType.SFlt16)
        {
            if (negative && t == FieldType.UFlt16)
                throw new InvalidOperationException($"{value} is negative; uflt16 holds [0, 1) (PS-420)");
            int bits = t == FieldType.SFlt16 ? 11 : 12;
            for (int e = 0; e < 16; e++)
            {
                var f = RoundScaled(m, bits + 15 - e);
                if (f < (BigInteger.One << bits))
                {
                    ulong word = ((ulong)e << bits) | (ulong)f;
                    return negative ? word | 0x8000 : word;
                }
            }
            throw new InvalidOperationException($"{value} is outside the range of {t} (PS-420)");
        }
        ulong sign = negative ? 0x800000UL : 0;
        if (value == 0) return sign;
        for (int e = 1; e < 127; e++)
        {
            if (ComparePow2(m, e - 62) < 0)
            {
                if (ComparePow2(m, e - 63) < 0) break;
                var f = RoundScaled(m, 16 + 63 - e) - 65536;
                if (f == 65536) continue;
                return sign | ((ulong)e << 16) | (ulong)f;
            }
        }
        if (ComparePow2(m, -62) >= 0)
            throw new InvalidOperationException($"{value} is outside the range of sflt24 (PS-420)");
        var sub = RoundScaled(m, 16 + 62);
        return sub >= 65536 ? sign | (1UL << 16) : sign | (ulong)sub;
    }

    /// <summary>PS-422 to PS-425: a named code applied to the unsigned integer read.</summary>
    public static long DecodeEncoding(ulong raw, string encoding, int size, string name)
    {
        int bits = size * 8;
        switch (encoding)
        {
            case "sign_magnitude":
            {
                ulong sign = 1UL << (bits - 1);
                return (raw & sign) != 0 ? -(long)(raw & (sign - 1)) : (long)raw;
            }
            case "bcd":
            {
                long result = 0;
                for (int i = size * 2 - 1; i >= 0; i--)
                {
                    ulong digit = (raw >> (4 * i)) & 0x0F;
                    if (digit > 9)
                        throw new InvalidOperationException($"field '{name}': invalid BCD digit {digit} (PS-424)");
                    result = result * 10 + (long)digit;
                }
                return result;
            }
            case "gray":
            {
                ulong result = raw;
                for (int shift = 1; shift < bits; shift <<= 1) result ^= result >> shift;
                return (long)result;
            }
        }
        return (long)raw;
    }

    /// <summary>The inverse, rejecting a value with no representation (PS-463).</summary>
    public static ulong EncodeEncoding(long value, string encoding, int size, string name)
    {
        int bits = size * 8;
        switch (encoding)
        {
            case "sign_magnitude":
            {
                ulong sign = 1UL << (bits - 1);
                if ((ulong)Math.Abs(value) > sign - 1)
                    throw new InvalidOperationException($"field '{name}': {value} has no {bits}-bit sign-magnitude form (PS-463)");
                return value < 0 ? sign | (ulong)(-value) : (ulong)value;
            }
            case "bcd":
            {
                if (value < 0 || value > Math.Pow(10, size * 2) - 1)
                    throw new InvalidOperationException($"field '{name}': {value} has no {bits}-bit BCD form (PS-463)");
                ulong result = 0;
                for (int shift = 0; value > 0; shift += 4)
                {
                    result |= (ulong)(value % 10) << shift;
                    value /= 10;
                }
                return result;
            }
            case "gray":
                return (ulong)value ^ ((ulong)value >> 1);
        }
        return (ulong)value;
    }
}
