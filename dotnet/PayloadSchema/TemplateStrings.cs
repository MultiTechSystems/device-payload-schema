// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using System.Numerics;
using System.Text.RegularExpressions;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-176 (PS-361 amended, PS-590 to PS-592): a `type: string` field declaring
/// `template` joins values decoded earlier into text and reads no bytes. `${name}` writes
/// what a key template writes (PS-532): a looked-up field's label, else the value bound;
/// `${name:format}` writes the number the reference binds (PS-523) in a part format of
/// PS-430 (<see cref="BitfieldParts.FormatNumber"/>); `$$` is `$`. Mirrors
/// render_string_template and string_template_errors in tools/schema_interpreter.py.
/// </summary>
internal static class TemplateStrings
{
    static readonly Regex Pattern = new(@"\$\$|\$\{(\w+)(?::([^}]*))?\}", RegexOptions.CultureInvariant);
    /// <summary>The names a template references, for PS-539's walk.</summary>
    internal static readonly Regex Reference = new(@"\$\{(\w+)(?::[^}]*)?\}", RegexOptions.CultureInvariant);

    /// <summary>PS-361 for a string field with neither `value` nor `template`, the prototype's text.</summary>
    internal static string NeitherMessage(string? name) =>
        $"Field '{name}': `type: string` is a literal that declares `value`, or a template that "
        + "declares `template`; a string read from the payload is `ascii` (PS-361)";

    /// <summary>
    /// The load-time rules, in the spec prototype's text: `template` sits on a `type: string`
    /// field with no `value` and is a string; a string field declares one of the two; no
    /// reference names an object or repeat <paramref name="aggregates"/> holds (PS-532); each
    /// format is a part format (PS-430).
    /// </summary>
    internal static void Check(YamlMappingNode fm, HashSet<string> aggregates)
    {
        string name = fm.Children.TryGetValue(new YamlScalarNode("name"), out var n)
            && n is YamlScalarNode ns ? ns.Value ?? "" : "<unnamed>";
        string? type = fm.Children.TryGetValue(new YamlScalarNode("type"), out var t)
            && t is YamlScalarNode ts ? ts.Value : null;
        bool hasValue = fm.Children.ContainsKey(new YamlScalarNode("value"));
        if (!fm.Children.TryGetValue(new YamlScalarNode("template"), out var node))
        {
            if (type == "string" && !hasValue)
                throw new InvalidOperationException(NeitherMessage(name));
            return;
        }
        if (type != "string" || hasValue || Meta.FromYaml(node) is not string template)
            throw new InvalidOperationException($"The template of '{name}' is a string on a "
                + "`type: string` field that declares no `value` (PS-361, PS-590)");
        foreach (Match m in Pattern.Matches(template))
        {
            if (!m.Groups[1].Success) continue;
            if (aggregates.Contains(m.Groups[1].Value))
                throw new InvalidOperationException($"The template of '{name}' references "
                    + $"`{m.Groups[1].Value}`, an object or repeat, which has no text (PS-532, PS-590)");
            if (m.Groups[2].Success)
                BitfieldParts.Parse(m.Groups[2].Value);                       // PS-430
        }
    }

    /// <summary>A value as a PS-591 warning writes it: a boolean as true or false, a number
    /// as JavaScript's String(n), anything else in single quotes.</summary>
    static string GivenText(object? value) => value switch
    {
        bool b => b ? "true" : "false",
        string s => "'" + s + "'",
        _ when Helpers.ToFloat64(value).ok => Wave5.RenderTemplateValue(value)
            ?? SchemaEncoder.JsNumber(Helpers.ToFloat64(value).Item2),
        _ => "'" + value + "'",
    };

    /// <summary>The integer a bound value holds exactly, or null where it holds none.</summary>
    static BigInteger? Integer(object? value)
    {
        switch (value)
        {
            case bool or string or null: return null;
            case sbyte or byte or short or ushort or int or uint or long:
                return new BigInteger(Convert.ToInt64(value, CultureInfo.InvariantCulture));
            case ulong u: return new BigInteger(u);
            case BigInteger big: return big;
        }
        var (ok, d) = Helpers.ToFloat64(value);
        if (!ok || !double.IsFinite(d) || Math.Floor(d) != d) return null;
        return new BigInteger(d);
    }

    /// <summary>The template's text, or <see cref="SchemaDecoder.Omitted"/> where a reference
    /// has no value (PS-592) or a format cannot write it (PS-591, with a warning).</summary>
    internal static object Render(SchemaField field, DecodeContext ctx)
    {
        bool absent = false;
        var text = Pattern.Replace(field.Template!, m =>
        {
            if (!m.Groups[1].Success) return "$";
            var reference = m.Groups[1].Value;
            if (!ctx.Variables.TryGetValue(reference, out var bound) || bound == null
                || ReferenceEquals(bound, SchemaDecoder.Omitted))
            {
                absent = true;
                return "";
            }
            if (!m.Groups[2].Success)
            {
                var value = ctx.Reported.TryGetValue(reference, out var reported) ? reported : bound;
                if (value == null || ReferenceEquals(value, SchemaDecoder.Omitted))
                {
                    absent = true;
                    return "";
                }
                return Wave5.RenderTemplateValue(value)
                    ?? throw new InvalidOperationException(
                        Wave5.TemplateShapeError(field.Template!, reference));
            }
            var format = m.Groups[2].Value;
            var (@base, width) = BitfieldParts.Parse(format);
            var number = Integer(bound);
            if (number == null || (@base.StartsWith("hex") && number.Value.Sign < 0))
            {
                ctx.Warnings.Add($"Field '{field.Name}': ${{{reference}:{format}}} is given "
                    + $"{GivenText(bound)}, which the format cannot write, so the field is absent (PS-591)");
                absent = true;
                return "";
            }
            return BitfieldParts.FormatNumber(number.Value.Sign < 0, BigInteger.Abs(number.Value), @base, width);
        });
        return absent ? SchemaDecoder.Omitted : text;
    }
}
