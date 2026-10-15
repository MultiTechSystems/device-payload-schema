// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using YamlDotNet.Core;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-177 (PS-534, PS-072, PS-593 to PS-595): an ascii field's `nul` mode (trailing,
/// stop, drop) and a `terminator` byte in place of `length`. Mirrors apply_nul_mode,
/// terminated_span and ascii_text_errors in tools/schema_interpreter.py.
/// </summary>
internal static class AsciiText
{
    /// <summary>PS-534: trailing removes trailing 0x00 bytes, stop ends the text at the first, drop removes all.</summary>
    internal static string ApplyNulMode(string text, string? mode)
    {
        if (mode == "stop")
        {
            int i = text.IndexOf('\0');
            return i < 0 ? text : text.Substring(0, i);
        }
        if (mode == "drop") return text.Replace("\0", "");
        return text.TrimEnd('\0');
    }

    /// <summary>A plain scalar that is a whole number (decimal or 0x hex), else null.</summary>
    internal static long? ParseInteger(YamlNode node)
    {
        if (node is not YamlScalarNode { Style: ScalarStyle.Plain } s || s.Value == null) return null;
        var text = s.Value;
        if (text.StartsWith("0x", StringComparison.OrdinalIgnoreCase))
            return long.TryParse(text.AsSpan(2), NumberStyles.AllowHexSpecifier,
                CultureInfo.InvariantCulture, out var h) ? h : null;
        return long.TryParse(text, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture,
            out var d) ? d : null;
    }

    /// <summary>PS-593: the number of text bytes before the terminator, or an underflow naming the field.</summary>
    internal static int TerminatedSpan(SchemaField field, DecodeContext ctx)
    {
        for (int i = ctx.Offset; i < ctx.Data.Length; i++)
            if (ctx.Data[i] == field.Terminator) return i - ctx.Offset;
        throw BufferUnderflowException.Text(
            $"no terminator byte 0x{field.Terminator:X2} before the end of the payload (PS-593)");
    }

    static string PyRepr(YamlNode node)
    {
        if (node is YamlScalarNode s)
        {
            if (s.Style != ScalarStyle.Plain) return $"'{s.Value}'";
            if (s.Value is "true" or "True" or "TRUE") return "True";
            if (s.Value is "false" or "False" or "FALSE") return "False";
            if (ParseInteger(s) is long n) return n.ToString(CultureInfo.InvariantCulture);
            if (s.Value is null or "" or "~" or "null" or "Null" or "NULL") return "None";
            if (double.TryParse(s.Value, NumberStyles.Float, CultureInfo.InvariantCulture, out _)) return s.Value;
            return $"'{s.Value}'";
        }
        return node.ToString();
    }

    /// <summary>The load rule over one field mapping, in the spec prototype's text.</summary>
    internal static void Check(YamlMappingNode fm)
    {
        if (!fm.Children.TryGetValue(new YamlScalarNode("type"), out var typeNode)) return;
        var name = fm.Children.TryGetValue(new YamlScalarNode("name"), out var n) ? (n as YamlScalarNode)?.Value : "None";
        var type = (typeNode as YamlScalarNode)?.Value;
        bool isAscii = string.Equals(type, "ascii", StringComparison.OrdinalIgnoreCase);
        bool hasNul = fm.Children.TryGetValue(new YamlScalarNode("nul"), out var nul);
        bool hasTerm = fm.Children.TryGetValue(new YamlScalarNode("terminator"), out var term);
        if (hasNul || hasTerm)
        {
            if (!isAscii)
                throw new InvalidOperationException($"Field '{name}': nul and terminator are defined "
                    + $"only on an ascii field, not on {type} (PS-534, PS-593)");
            if (hasNul)
            {
                var mode = (nul as YamlScalarNode)?.Value;
                if (mode is not ("trailing" or "stop" or "drop") || nul is not YamlScalarNode)
                    throw new InvalidOperationException($"Field '{name}': nul must be trailing, stop or "
                        + $"drop, not {PyRepr(nul!)} (PS-534)");
            }
            if (hasTerm)
            {
                var value = ParseInteger(term!);
                if (value is null || value < 0 || value > 255)
                    throw new InvalidOperationException($"Field '{name}': terminator must be a byte value "
                        + $"0 to 255, not {PyRepr(term!)} (PS-593)");
                if (fm.Children.ContainsKey(new YamlScalarNode("length")))
                    throw new InvalidOperationException($"Field '{name}': an ascii field declares length "
                        + "or terminator, not both (PS-593)");
            }
        }
        if (isAscii && !fm.Children.ContainsKey(new YamlScalarNode("length")) && !hasTerm)
            throw new InvalidOperationException($"Field '{name}': ascii type requires 'length' field, "
                + "or 'terminator' (PS-072)");
    }
}
