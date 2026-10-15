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

    /// <summary>CR-2026-183 (PS-096, PS-605, PS-606): a value's segments, one per part. The
    /// declared prefix is required, the rest split on the delimiter, or by each part's `:N`
    /// width where the delimiter is empty; a segment count other than the part count is
    /// rejected, never filled or cut. Mirrors bitfield_segments and split_by_width.</summary>
    internal static List<string> Segments(string? name, string? prefix, string? delimiter,
        List<List<object>> parts, string value)
    {
        var text = value;
        if (!string.IsNullOrEmpty(prefix))
        {
            if (!text.StartsWith(prefix, StringComparison.Ordinal))
                throw new InvalidOperationException($"Field '{name}': {Meta.PyRepr(value)} does not "
                    + $"begin with its prefix {Meta.PyRepr(prefix)} (PS-605)");
            text = text[prefix.Length..];
        }
        var segments = delimiter == ""
            ? SplitByWidth(text, parts, name, value)
            : text.Split(delimiter ?? ".").ToList();
        if (segments.Count != parts.Count)
            throw new InvalidOperationException($"Field '{name}': {Meta.PyRepr(value)} has "
                + $"{segments.Count} segment(s) for {parts.Count} part(s) (PS-605)");
        return segments;
    }

    /// <summary>PS-606: split by width; a part lacking a width for its largest value, or an
    /// sdecimal part, makes the field unencodable (it still decodes).</summary>
    static List<string> SplitByWidth(string text, List<List<object>> parts, string? name, string value)
    {
        var widths = new List<int>();
        foreach (var part in parts)
        {
            var (_, bits) = Helpers.ToInt(part[1]);
            var (b, width) = Parse(part.Count > 2 ? part[2] : "decimal");
            int largest = bits > 0 ? FormatNumber(false, (BigInteger.One << bits) - 1, b, 0).Length : 1;
            if (b == "sdecimal" || width == 0 || largest > width)
                throw new InvalidOperationException($"Field '{name}': with an empty delimiter every "
                    + "part needs a :N width that holds its largest value, and none may be sdecimal, "
                    + "so this field cannot be encoded (PS-606)");
            widths.Add(width);
        }
        if (text.Length != widths.Sum())
            throw new InvalidOperationException($"Field '{name}': {Meta.PyRepr(value)} has "
                + $"{text.Length} characters for parts of widths [{string.Join(", ", widths)}] (PS-606)");
        var segments = new List<string>();
        int at = 0;
        foreach (var w in widths)
        {
            segments.Add(text.Substring(at, w));
            at += w;
        }
        return segments;
    }
}
