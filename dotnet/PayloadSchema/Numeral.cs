// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Text.RegularExpressions;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-150 (PS-560, PS-561): an ascii field with `numeral: decimal|hex` reports its
/// digit text as an integer, strictly, and writes it back canonically. Mirrors
/// decode_numeral, encode_numeral and numeral_errors in tools/schema_interpreter.py.
/// </summary>
internal static class Numeral
{
    static readonly Regex Decimal = new("^[+-]?[0-9]+$", RegexOptions.CultureInvariant);
    static readonly Regex Hex = new("^[0-9A-Fa-f]+$", RegexOptions.CultureInvariant);

    static Regex? PatternOf(string? numeral) => numeral switch
    {
        "decimal" => Decimal,
        "hex" => Hex,
        _ => null,
    };

    /// <summary>The integer the whole text spells, or an error naming the field; never a prefix of it.</summary>
    internal static long Decode(string? name, string text, string numeral)
    {
        long value = 0;
        bool ok = PatternOf(numeral)!.IsMatch(text)
            && (numeral == "hex"
                ? long.TryParse(text, System.Globalization.NumberStyles.AllowHexSpecifier,
                    System.Globalization.CultureInfo.InvariantCulture, out value)
                : long.TryParse(text, System.Globalization.NumberStyles.AllowLeadingSign,
                    System.Globalization.CultureInfo.InvariantCulture, out value));
        if (!ok)
            throw new FieldErrorException($"Field '{name}': '{text}' is not a {numeral} numeral (PS-560)");
        return value;
    }

    /// <summary>
    /// The digits of the value in the declared base, uppercase hex, filled with 0 to the
    /// length after any sign. A negative hex value, or one that does not fit, is a PS-168
    /// error naming the field. <paramref name="fixedLength"/> is null where it is not fixed.
    /// </summary>
    internal static string Encode(string? name, double value, int? fixedLength, string numeral)
    {
        double n = Math.Round(value, MidpointRounding.ToEven);                // PS-552
        double abs = Math.Abs(n);
        var digits = numeral == "hex"
            ? ((ulong)abs).ToString("X")
            : ((ulong)abs).ToString(System.Globalization.CultureInfo.InvariantCulture);
        var sign = n < 0 ? "-" : "";
        int width = fixedLength ?? sign.Length + digits.Length;
        if (fixedLength == null && n < 0 && numeral == "hex")                    // PS-168, PS-595
            throw new FieldErrorException($"Field '{name}': {SchemaEncoder.JsNumber(value)} is negative, "
                + "which a hex numeral cannot write (PS-168)");
        if ((n < 0 && numeral == "hex") || sign.Length + digits.Length > width)
            throw new FieldErrorException($"Field '{name}': {SchemaEncoder.JsNumber(value)} does not fit "
                + $"{fixedLength ?? digits.Length} {numeral} digits (PS-168)");
        return sign + new string('0', width - sign.Length - digits.Length) + digits;
    }

    /// <summary>PS-560's load rule over one field mapping, in the spec prototype's text.</summary>
    internal static void Check(YamlMappingNode fm)
    {
        if (!fm.Children.TryGetValue(new YamlScalarNode("numeral"), out var numeralNode)
            || !fm.Children.TryGetValue(new YamlScalarNode("type"), out var typeNode))
            return;
        var name = fm.Children.TryGetValue(new YamlScalarNode("name"), out var n)
            ? ((YamlScalarNode)n).Value : "None";
        var type = ((YamlScalarNode)typeNode).Value;
        if (!string.Equals(type, "ascii", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException($"Field '{name}': numeral is defined only on an ascii "
                + $"field, not on {type} (PS-560)");
        var numeral = (numeralNode as YamlScalarNode)?.Value;
        if (PatternOf(numeral) == null)
        {
            var shown = numeralNode is YamlScalarNode { Style: YamlDotNet.Core.ScalarStyle.Plain } plain
                && double.TryParse(plain.Value, out _) ? plain.Value : $"'{numeral}'";
            throw new InvalidOperationException($"Field '{name}': numeral must be decimal or hex, not "
                + $"{shown} (PS-560)");
        }
    }
}
