// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Buffers.Binary;

namespace PayloadSchema;

/// <summary>
/// An error whose text already names its field in full, reported verbatim: an enclosing
/// construct must not prefix it. Mirrors FieldError in tools/schema_interpreter.py.
/// </summary>
public sealed class FieldErrorException : InvalidOperationException
{
    public FieldErrorException(string message) : base(message) { }
}

public static class Helpers
{
    /// <summary>
    /// PS-534, PS-538 (CR-2026-114): each byte of an `ascii` value is the ISO 8859-1
    /// character of the same number, so the value is lossless and re-encodes to the same
    /// bytes (PS-537); trailing 0x00 padding only is dropped. A byte above 0x7F is reported
    /// with PS-073's warning. Encoding.ASCII wrote `?` for it. Mirrors decode_ascii in
    /// tools/schema_interpreter.py.
    /// </summary>
    public static string DecodeAscii(string? name, ReadOnlySpan<byte> raw, List<string> warnings)
    {
        var chars = new char[raw.Length];
        bool high = false;
        for (int i = 0; i < raw.Length; i++)
        {
            chars[i] = (char)raw[i];
            if (raw[i] > 0x7F) high = true;
        }
        if (high)
            warnings.Add($"Field '{name}': a byte above 0x7F in an ascii value (PS-073, PS-538)");
        return new string(chars).TrimEnd('\0');
    }

    /// <summary>
    /// PS-537 (CR-2026-114): a character above U+00FF, or a value longer than a fixed
    /// <paramref name="length"/>, is rejected naming the field; U+0080..U+00FF is written as
    /// the byte of the same number with a warning; a short value is padded with 0x00. A null
    /// length is not fixed (`remaining`, a reference), and the value supplies it. Mirrors
    /// encode_ascii in tools/schema_interpreter.py. UTF-8 was written and then truncated,
    /// which could split a character ("ABCé" to 414243c3).
    /// </summary>
    public static byte[] EncodeAscii(string? name, string text, int? length, List<string> warnings)
    {
        foreach (var ch in text)
            if (ch > 0xFF)
                throw new FieldErrorException($"Field '{name}': an ascii value holds a character "
                    + "above U+00FF, which has no byte (PS-537)");
        if (length is int fixedLength && text.Length > fixedLength)
            throw new FieldErrorException($"Field '{name}': an ascii value of {text.Length} bytes "
                + $"is longer than its length of {fixedLength} (PS-537)");
        var output = new byte[length ?? text.Length];
        bool high = false;
        for (int i = 0; i < text.Length; i++)
        {
            output[i] = (byte)text[i];
            if (text[i] > 0x7F) high = true;
        }
        if (high)
            warnings.Add($"Field '{name}': a character above U+007F in an ascii value (PS-537)");
        return output;
    }

    public static ulong DecodeUint(ReadOnlySpan<byte> data, string endian)
    {
        ulong val = 0;
        if (endian == "little")
        {
            for (int i = data.Length - 1; i >= 0; i--)
                val = (val << 8) | data[i];
        }
        else
        {
            for (int i = 0; i < data.Length; i++)
                val = (val << 8) | data[i];
        }
        return val;
    }

    public static long DecodeSint(ReadOnlySpan<byte> data, string endian)
    {
        ulong uval = DecodeUint(data, endian);
        int bits = data.Length * 8;
        ulong signBit = 1UL << (bits - 1);
        if (uval >= signBit)
        {
            // C# masks a shift count to the operand's width, so `1L << 64` is `1L << 0`,
            // and the subtraction wrapped s64 minimum to s64 maximum: 8000000000000000
            // decoded as 9223372036854775807. At a full 64 bits the unsigned bit pattern
            // already is the two's-complement value (PS-294).
            if (bits >= 64) return unchecked((long)uval);
            return (long)uval - (1L << bits);
        }
        return (long)uval;
    }

    public static double DecodeFloat(ReadOnlySpan<byte> data, int size, string endian)
    {
        return size switch
        {
            2 => Float16ToFloat64(endian == "little"
                ? BinaryPrimitives.ReadUInt16LittleEndian(data)
                : BinaryPrimitives.ReadUInt16BigEndian(data)),
            4 => (double)BitConverter.Int32BitsToSingle(
                (int)(endian == "little"
                    ? BinaryPrimitives.ReadUInt32LittleEndian(data)
                    : BinaryPrimitives.ReadUInt32BigEndian(data))),
            8 => BitConverter.Int64BitsToDouble(
                (long)(endian == "little"
                    ? BinaryPrimitives.ReadUInt64LittleEndian(data)
                    : BinaryPrimitives.ReadUInt64BigEndian(data))),
            _ => throw new ArgumentException($"Unsupported float size: {size}")
        };
    }

    public static double Float16ToFloat64(ushort u16)
    {
        int sign = (u16 >> 15) & 1;
        int exp = (u16 >> 10) & 0x1f;
        int mant = u16 & 0x3ff;

        double val;
        if (exp == 0)
            val = Math.Pow(2, -14) * mant / 1024.0;
        else if (exp == 31)
            return mant != 0 ? double.NaN : double.PositiveInfinity * (sign == 1 ? -1 : 1);
        else
            val = Math.Pow(2, exp - 15) * (1.0 + mant / 1024.0);

        return sign == 1 ? -val : val;
    }

    public static int DecodeBits(byte byteVal, int bitOffset, int bits)
    {
        int mask = (1 << bits) - 1;
        return (byteVal >> bitOffset) & mask;
    }

    public static byte[] EncodeUint(ulong val, int length, string endian)
    {
        var buf = new byte[length];
        if (endian == "little")
        {
            for (int i = 0; i < length; i++)
            {
                buf[i] = (byte)(val & 0xFF);
                val >>= 8;
            }
        }
        else
        {
            for (int i = length - 1; i >= 0; i--)
            {
                buf[i] = (byte)(val & 0xFF);
                val >>= 8;
            }
        }
        return buf;
    }

    public static byte[] EncodeSint(long val, int length, string endian)
    {
        // Two's complement is the low `length` bytes of the value's own bit pattern.
        // This added 1L << (length * 8), and C# takes a long shift count modulo 64, so
        // for an 8-byte field it added 1: an s64 of -9 was written as -8.
        return EncodeUint(unchecked((ulong)val), length, endian);
    }

    public static byte[] EncodeFloat32(float val, string endian)
    {
        var buf = new byte[4];
        uint bits = BitConverter.SingleToUInt32Bits(val + 0.0f);   // PS-566: -0 + 0 is 0
        if (bits == 0x80000000u) bits = 0;   // CR-2026-157: a negative value that rounds to zero
        if (endian == "little")
            BinaryPrimitives.WriteUInt32LittleEndian(buf, bits);
        else
            BinaryPrimitives.WriteUInt32BigEndian(buf, bits);
        return buf;
    }

    public static byte[] EncodeFloat64(double val, string endian)
    {
        var buf = new byte[8];
        ulong bits = BitConverter.DoubleToUInt64Bits(val + 0.0);   // PS-566
        if (endian == "little")
            BinaryPrimitives.WriteUInt64LittleEndian(buf, bits);
        else
            BinaryPrimitives.WriteUInt64BigEndian(buf, bits);
        return buf;
    }

    /// <summary>
    /// IEEE 754 half-precision, the inverse of <see cref="Float16ToFloat64"/>. Encoding an
    /// f16 field had no primitive to reach for: EncodeFloat32 and EncodeFloat64 existed and
    /// the two-byte form did not.
    /// </summary>
    public static byte[] EncodeFloat16(double val, string endian)
    {
        // .NET's Half does the rounding, including subnormals and the overflow to infinity,
        // so this is a conversion rather than a hand-rolled bit shuffle.
        ushort bits = BitConverter.HalfToUInt16Bits((Half)(val + 0.0));   // PS-566
        if (bits == 0x8000) bits = 0;   // CR-2026-157
        return EncodeUint(bits, 2, endian);
    }

    public static int InferLengthFromType(FieldType type) => type switch
    {
        FieldType.U8 or FieldType.S8 => 1,
        FieldType.U16 or FieldType.S16 or FieldType.F16 or FieldType.BF16 => 2,
        FieldType.U24 or FieldType.S24 => 3,
        FieldType.U32 or FieldType.S32 or FieldType.F32
            or FieldType.U32LE16 or FieldType.S32LE16 or FieldType.F32LE16
            or FieldType.U32BE16LE or FieldType.S32BE16LE or FieldType.F32BE16LE => 4,
        FieldType.UFlt16 or FieldType.SFlt16 => 2,
        FieldType.SFlt24 => 3,
        FieldType.U64 or FieldType.S64 or FieldType.F64 => 8,
        _ => 1
    };

    /// <summary>
    /// PS-513 (CR-2026-104): a boolean label matches only a boolean input, and a boolean
    /// input only a boolean label - `true` is not 1, and "true" is not `true`. A string
    /// matches only an equal string, and a number any number equal to it.
    /// </summary>
    public static bool SameLabel(object? label, object? value)
    {
        if (label is bool || value is bool)
            return label is bool l && value is bool v && l == v;
        if (label is string || value is string)
            return label is string ls && value is string vs && ls == vs;
        var (lok, ln) = ToFloat64(label);
        var (vok, vn) = ToFloat64(value);
        return lok && vok && ln == vn;
    }

    public static (bool ok, double value) ToFloat64(object? v)
    {
        return v switch
        {
            double d => (true, d),
            float f => (true, f),
            int i => (true, i),
            long l => (true, l),
            ulong u => (true, u),
            uint ui => (true, ui),
            short s => (true, s),
            ushort us => (true, us),
            byte b => (true, b),
            sbyte sb => (true, sb),
            decimal dec => (true, (double)dec),
            _ => (false, 0)
        };
    }

    public static (bool ok, int value) ToInt(object? v)
    {
        var (ok, d) = ToFloat64(v);
        return ok ? (true, (int)d) : (false, 0);
    }

    /// <summary>
    /// A value as a 64-bit integer, for comparing a discriminator with a case key. A
    /// u32 above 0x7FFFFFFF does not fit an int, and (int) of an out-of-range double is
    /// int.MinValue on x64, so ToInt made 0xFFFFFFFF, 0x80000001 and 2147483648 equal.
    /// Integral types convert exactly; a double truncates as ToInt does, and one outside
    /// long's range is not converted.
    /// </summary>
    public static (bool ok, long value) ToLong(object? v)
    {
        switch (v)
        {
            case long l: return (true, l);
            case int i: return (true, i);
            case uint ui: return (true, ui);
            case ulong ul when ul <= long.MaxValue: return (true, (long)ul);
            case short s: return (true, s);
            case ushort us: return (true, us);
            case byte b: return (true, b);
            case sbyte sb: return (true, sb);
        }
        var (ok, d) = ToFloat64(v);
        if (!ok || double.IsNaN(d) || d < -9.2233720368547758E18 || d >= 9.2233720368547758E18)
            return (false, 0);
        return (true, (long)d);
    }

    public static object FormatBytes(byte[] data, string? format, string? separator)
    {
        format ??= "hex";
        return format switch
        {
            "hex" or "hex:lower" => separator != null
                ? string.Join(separator, data.Select(b => b.ToString("x2")))
                : Convert.ToHexString(data).ToLowerInvariant(),
            "hex:upper" => separator != null
                ? string.Join(separator, data.Select(b => b.ToString("X2")))
                : Convert.ToHexString(data),
            "base64" => Convert.ToBase64String(data),
            "array" => data.Select(b => (object)(double)b).ToList(),
            _ => Convert.ToHexString(data).ToLowerInvariant()
        };
    }

    /// <summary>
    /// The field type a schema's `type:` string names, or Unknown.
    ///
    /// The vocabulary is closed (CR-2026-037): the alias table is exhaustive (PS-326) and
    /// names are case-sensitive (PS-333). This lowercased its input and accepted `byte`,
    /// `uint`, `float16`-`float64`, `bits`, `switch`, `ctrl-switch`, `tlv` and
    /// `version_string`, none of which is a type of the specification, while missing all
    /// ten PS-049 aliases, `base64`, `udec`, `sdec` and `integer`.
    /// </summary>
    /// <summary>The values of a quoted list case key, "[1, 2, 0x10]" (PS-398), or null.</summary>
    public static List<long>? ParseListCaseKey(string text)
    {
        text = text.Trim();
        if (!text.StartsWith('[') || !text.EndsWith(']')) return null;
        var values = new List<long>();
        foreach (var raw in text[1..^1].Split(','))
        {
            var part = raw.Trim();
            if (part.Length == 0) continue;
            try
            {
                values.Add(part.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
                    ? Convert.ToInt64(part[2..], 16) : long.Parse(part));
            }
            catch (FormatException) { return null; }
        }
        return values;
    }

    public static FieldType ParseFieldType(string typeStr)
    {
        var baseType = typeStr.Contains('[') ? typeStr[..typeStr.IndexOf('[')] : typeStr;

        return baseType switch
        {
            "u8" or "uint8" => FieldType.U8,
            "u16" or "uint16" => FieldType.U16,
            "u24" or "uint24" => FieldType.U24,
            "u32" or "uint32" => FieldType.U32,
            "u64" or "uint64" => FieldType.U64,
            "u32le16" => FieldType.U32LE16,
            "f32le16" => FieldType.F32LE16,
            "u32be16le" => FieldType.U32BE16LE,
            "s32be16le" => FieldType.S32BE16LE,
            "f32be16le" => FieldType.F32BE16LE,
            "uflt16" => FieldType.UFlt16,
            "sflt16" => FieldType.SFlt16,
            "sflt24" => FieldType.SFlt24,
            "s32le16" => FieldType.S32LE16,
            "s8" or "i8" or "int8" => FieldType.S8,
            "s16" or "i16" or "int16" => FieldType.S16,
            "s24" or "i24" or "int24" => FieldType.S24,
            "s32" or "i32" or "int32" => FieldType.S32,
            "s64" or "i64" or "int64" => FieldType.S64,
            "f16" => FieldType.F16,
            "bf16" => FieldType.BF16,
            "f32" => FieldType.F32,
            "f64" => FieldType.F64,
            "udec" => FieldType.UDec,
            "sdec" => FieldType.SDec,
            "bool" => FieldType.Bool,
            "ascii" => FieldType.Ascii,
            "hex" => FieldType.Hex,
            "base64" => FieldType.Base64,
            "bytes" => FieldType.Bytes,
            "skip" => FieldType.Skip,
            "string" => FieldType.String,
            "boolean" => FieldType.Boolean,
            "number" => FieldType.Number,
            "integer" => FieldType.Integer,
            "object" => FieldType.Object,
            "repeat" => FieldType.Repeat,
            "enum" => FieldType.Enum,
            "bitfield_string" => FieldType.BitfieldString,
            _ => FieldType.Unknown
        };
    }

    /// <summary>Parse bitfield range from type string like "u8[3:7]" -> (3, 7)</summary>
    public static (int start, int end)? ParseBitRange(string typeStr)
    {
        int idx = typeStr.IndexOf('[');
        if (idx < 0) return null;
        var range = typeStr[(idx + 1)..typeStr.IndexOf(']')];
        var parts = range.Split(':');
        if (parts.Length != 2) return null;
        return (int.Parse(parts[0]), int.Parse(parts[1]));
    }

    /// <summary>Parse endian prefix from type like "le_u16" -> ("little", "u16")</summary>
    public static double EvaluatePolynomial(double[] coeffs, double x)
    {
        if (coeffs.Length == 0) return 0;
        double result = coeffs[0];
        for (int i = 1; i < coeffs.Length; i++)
            result = result * x + coeffs[i];
        return result;
    }
}
