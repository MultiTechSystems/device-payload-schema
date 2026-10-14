// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using System.Text.RegularExpressions;

namespace PayloadSchema;

/// <summary>
/// A range case key, <c>"2..5"</c> or <c>"12.1..35.4"</c> (PS-398): two numbers, each a
/// decimal integer, a hexadecimal integer or a decimal with a fraction or exponent, either
/// of them negative. A bound is compared as the number the YAML spells, an integer staying
/// an integer, so <c>"12.1..35.4"</c> holds a computed 12.1 and 35.4 exactly.
/// </summary>
internal sealed class RangeKey
{
    const string Number = @"[+-]?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)";
    static readonly Regex Key = new(@"^\s*(" + Number + @")\s*\.\.\s*(" + Number + @")\s*$",
        RegexOptions.CultureInvariant);

    readonly double _lo, _hi;
    readonly long? _loInt, _hiInt;

    RangeKey(double lo, double hi, long? loInt, long? hiInt)
    {
        _lo = lo; _hi = hi; _loInt = loInt; _hiInt = hiInt;
    }

    /// <summary>The bounds of a range case key, or null where the text is not one.</summary>
    internal static RangeKey? Parse(string text)
    {
        var m = Key.Match(text);
        if (!m.Success) return null;
        var loInt = Integer(m.Groups[1].Value);
        var hiInt = Integer(m.Groups[2].Value);
        return new RangeKey(AsNumber(m.Groups[1].Value, loInt), AsNumber(m.Groups[2].Value, hiInt),
            loInt, hiInt);
    }

    static long? Integer(string text)
    {
        var digits = text.StartsWith('+') ? text[1..] : text;
        var negative = digits.StartsWith('-');
        if (negative) digits = digits[1..];
        var style = NumberStyles.None;
        if (digits.Length > 2 && (digits.StartsWith("0x") || digits.StartsWith("0X")))
        {
            digits = digits[2..];
            style = NumberStyles.AllowHexSpecifier;
        }
        else if (digits.IndexOfAny(new[] { '.', 'e', 'E' }) >= 0) return null;
        if (!long.TryParse(digits, style, CultureInfo.InvariantCulture, out var n) || n < 0) return null;
        return negative ? -n : n;
    }

    static double AsNumber(string text, long? integer) =>
        integer is { } n ? n : double.Parse(text, CultureInfo.InvariantCulture);

    internal bool Contains(long value) =>
        _loInt is { } lo && _hiInt is { } hi ? value >= lo && value <= hi : value >= _lo && value <= _hi;

    internal bool Contains(double value) => value >= _lo && value <= _hi;

    /// <summary>Why a case key spelled like a range is not one (PS-398, PS-151), or null.</summary>
    internal static string? Error(string key)
    {
        if (!key.Contains("..") || key.TrimStart().StartsWith('[')) return null;
        var range = Parse(key);
        if (range is null)
            return "a range key is `start..end` with two numbers, each an integer or a decimal, "
                   + "such as 2..5 or 12.1..35.4 (PS-398)";
        if (range._lo > range._hi) return "a range key has start <= end (PS-151, PS-398)";
        return null;
    }
}
