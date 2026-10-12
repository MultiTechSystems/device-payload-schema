// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

namespace PayloadSchema;

/// <summary>
/// CR-2026-149 (PS-558, PS-559): bf16, the upper half of an IEEE 754 binary32. Mirrors
/// decode_bf16 and encode_bf16 in tools/schema_interpreter.py.
/// </summary>
internal static class Bf16
{
    /// <summary>The largest finite bf16, 0x7F7F: (2 - 2^-7) * 2^127.</summary>
    internal static readonly double Max = (2 - 1.0 / 128) * Math.Pow(2, 127);

    /// <summary>The binary32 whose upper half is the word and whose lower half is zero.</summary>
    internal static double Decode(ulong word) =>
        BitConverter.Int32BitsToSingle((int)((uint)(word & 0xFFFF) << 16));

    /// <summary>
    /// The nearest bf16 to the binary64 value, a tie to the even fraction, rounded once and
    /// never through a binary32. A finite value that rounds past the largest bf16 is a PS-168
    /// error naming the field; NaN and the infinities are their words.
    /// </summary>
    internal static ulong Encode(double value, string? name)
    {
        if (double.IsNaN(value)) return 0x7FC0;
        ulong sign = BitConverter.DoubleToInt64Bits(value) < 0 ? 0x8000UL : 0;
        double magnitude = Math.Abs(value);
        if (double.IsInfinity(magnitude)) return sign | 0x7F80;
        ulong word;
        int exponent = magnitude == 0 ? 0 : Math.ILogB(magnitude);   // magnitude = 1.f * 2^exponent
        if (magnitude == 0 || exponent < -126)
        {
            word = (ulong)Math.Round(Math.ScaleB(magnitude, 133), MidpointRounding.ToEven);
        }
        else
        {
            long fraction = (long)Math.Round(Math.ScaleB(magnitude, -exponent) * 128,
                MidpointRounding.ToEven);                              // 128..256, exact
            if (fraction == 256) { fraction = 128; exponent += 1; }
            word = ((ulong)(exponent + 127) << 7) | (ulong)(fraction - 128);
        }
        if (word >= 0x7F80)
            throw new InvalidOperationException(
                $"Field '{name}': {SchemaEncoder.JsNumber(value)} is outside the range of bf16, "
                + $"{SchemaEncoder.JsNumber(-Max)} to {SchemaEncoder.JsNumber(Max)} (PS-168)");
        return sign | word;
    }
}
