// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using System.Numerics;
using System.Text.RegularExpressions;

namespace PayloadSchema;

/// <summary>
/// CR-2026-170 (PS-430, PS-582, PS-096, PS-583): a bitfield_string part format is decimal,
/// sdecimal, hex or hex:upper, optionally followed by `:N`, a minimum width from 1 to 32 that
/// never cuts. `sdecimal` reads its bits as two's complement; an encoder rejects a segment the
/// part's width cannot hold. Mirrors parse_part_format, format_part_number,
/// format_bitfield_part and parse_bitfield_segment in tools/schema_interpreter.py.
/// <see cref="FormatNumber"/> is the one formatter, for CR-2026-176's templates too.
/// </summary>
internal static class BitfieldParts
{
    static readonly Regex Format =
        new("^(decimal|sdecimal|hex:upper|hex)(?::([1-9][0-9]?))?$", RegexOptions.CultureInvariant);
    static readonly Regex DecimalSegment = new("^[0-9]+$", RegexOptions.CultureInvariant);
    static readonly Regex SdecimalSegment = new("^-?[0-9]+$", RegexOptions.CultureInvariant);
    static readonly Regex HexSegment = new("^[0-9A-Fa-f]+$", RegexOptions.CultureInvariant);

    /// <summary>A part format's base and minimum width (0 where it declares none), or PS-430's
    /// rejection in the spec prototype's text.</summary>
    internal static (string Base, int Width) Parse(object? format)
    {
        if (format is string text)
        {
            var m = Format.Match(text);
            if (m.Success)
            {
                int width = m.Groups[2].Success ? int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture) : 0;
                if (width <= 32) return (m.Groups[1].Value, width);
            }
        }
        throw new InvalidOperationException("bitfield_string part format must be decimal, sdecimal, hex or "
            + $"hex:upper, with an optional :N width from 1 to 32, not {Meta.PyRepr(format)} (PS-430)");
    }

    /// <summary>A magnitude in a part format's base - hex lower or upper case, else decimal -
    /// with a leading `-` where negative, zero-filled on the left to `width` digits and never
    /// cut (PS-430, PS-582).</summary>
    internal static string FormatNumber(bool negative, BigInteger magnitude, string @base, int width)
    {
        string digits = @base switch
        {
            "hex" => magnitude.IsZero ? "0" : magnitude.ToString("x", CultureInfo.InvariantCulture).TrimStart('0'),
            "hex:upper" => magnitude.IsZero ? "0" : magnitude.ToString("X", CultureInfo.InvariantCulture).TrimStart('0'),
            _ => magnitude.ToString(CultureInfo.InvariantCulture),
        };
        return (negative ? "-" : "") + digits.PadLeft(width, '0');
    }

    /// <summary>One part from its unsigned bits; sdecimal is two's complement of the part's
    /// width (PS-582).</summary>
    internal static string FormatPart(ulong raw, int bits, object? format)
    {
        var (b, width) = Parse(format);
        var value = new BigInteger(raw);
        if (b == "sdecimal" && bits > 0 && !(value >> (bits - 1)).IsEven)
            return FormatNumber(true, (BigInteger.One << bits) - value, b, width);
        return FormatNumber(false, value, b, width);
    }

    /// <summary>One segment's unsigned bits (PS-096, PS-431, PS-583): leading zeros are
    /// accepted, hex without regard to case, a `-` only on sdecimal; a value the part's width
    /// cannot hold is rejected naming the field and the segment, never masked.</summary>
    internal static ulong ParseSegment(string segment, int bits, object? format, string? name)
    {
        var (b, _) = Parse(format);
        var text = segment.Trim();
        var pattern = b == "sdecimal" ? SdecimalSegment : b == "decimal" ? DecimalSegment : HexSegment;
        if (!pattern.IsMatch(text))
            throw new InvalidOperationException(
                $"Field '{name}': segment {Meta.PyRepr(segment)} is not a {b} part (PS-096)");
        var number = b.StartsWith("hex")
            ? BigInteger.Parse("0" + text, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture)
            : BigInteger.Parse(text, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);
        BigInteger low = BigInteger.Zero, high = (BigInteger.One << bits) - 1;
        if (b == "sdecimal")
        {
            var half = BigInteger.One << (bits - 1);
            low = -half;
            high = half - 1;
        }
        if (number < low || number > high)
            throw new InvalidOperationException($"Field '{name}': segment {Meta.PyRepr(segment)} does not fit a "
                + $"{bits}-bit {b} part, {low.ToString(CultureInfo.InvariantCulture)} to "
                + $"{high.ToString(CultureInfo.InvariantCulture)} (PS-583)");
        if (number.Sign < 0) number += BigInteger.One << bits;
        return (ulong)number;
    }
}
