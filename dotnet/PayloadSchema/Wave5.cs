// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace PayloadSchema;

/// <summary>
/// 0.5.2 wave 5: a lookup default that names the value it could not map (CR-2026-060).
/// Each method mirrors the function of the same purpose in tools/schema_interpreter.py.
/// </summary>
static class Wave5
{
    /// <summary>What a mapping's <c>default</c> substitutes with the value (PS-406).</summary>
    public const string ValueToken = "${value}";

    const string Number = @"(-?\d+(?:\.\d+)?(?:e[-+]?\d+)?)";

    /// <summary>The field's mapping default where it carries ${value}, else null (PS-408).</summary>
    public static string? Template(SchemaField field) =>
        field.LookupDefault is { } fallback && !field.LookupIsSequence && fallback.Contains(ValueToken)
            ? fallback : null;

    /// <summary>
    /// A value written as JavaScript's String(number) writes it, which is what the reference
    /// interpreter and the generated codec write (PS-406, PS-280): the shortest round-tripping
    /// digits, fixed for 1e-6 &lt;= |v| &lt; 1e21, and 1e-7 / 1.5e+21 outside that range.
    /// </summary>
    public static string FormatLookupValue(double v)
    {
        if (v == 0) return "0";
        // "R" gives the shortest round-tripping digits, in .NET's own layout ("1.5E-07").
        var text = Math.Abs(v).ToString("R", CultureInfo.InvariantCulture);
        int exp = 0, e = text.IndexOfAny(new[] { 'E', 'e' });
        if (e >= 0) { exp = int.Parse(text[(e + 1)..], CultureInfo.InvariantCulture); text = text[..e]; }
        int point = text.IndexOf('.');
        int intLen = point >= 0 ? point : text.Length;
        var raw = text.Replace(".", "");
        // value = digits x 10^scale, with no leading or trailing zeros in digits.
        var digits = raw.TrimStart('0');
        int scale = intLen - raw.Length + exp;
        int trailing = digits.Length - digits.TrimEnd('0').Length;
        digits = digits.TrimEnd('0');
        scale += trailing;
        int k = digits.Length, n = k + scale;          // value = 0.digits x 10^n
        var sign = v < 0 ? "-" : "";
        double abs = Math.Abs(v);
        if (abs >= 1e-6 && abs < 1e21)
        {
            if (n <= 0) return sign + "0." + new string('0', -n) + digits;
            if (n >= k) return sign + digits + new string('0', n - k);
            return sign + digits[..n] + "." + digits[n..];
        }
        var mantissa = k > 1 ? digits[0] + "." + digits[1..] : digits;
        int exponent = n - 1;
        return sign + mantissa + "e" + (exponent < 0 ? "-" : "+") + Math.Abs(exponent);
    }

    /// <summary>The number a ${value} default wrote into <paramref name="text"/>, or null (PS-409).</summary>
    public static double? MatchTemplate(string template, string text)
    {
        var parts = template.Split(ValueToken);
        var pattern = new StringBuilder("^").Append(Regex.Escape(parts[0])).Append(Number);
        for (int i = 1; i < parts.Length - 1; i++)
            pattern.Append(Regex.Escape(parts[i])).Append(@"\1");
        pattern.Append(Regex.Escape(parts[^1])).Append('$');
        var m = Regex.Match(text, pattern.ToString());
        return m.Success ? double.Parse(m.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture) : null;
    }
}
