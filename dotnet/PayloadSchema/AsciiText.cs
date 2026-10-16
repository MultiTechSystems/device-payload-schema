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
    /// <remarks>PS-619 (CR-2026-188): returns the text's length and the terminator bytes
    /// consumed, 0 with <c>consume_terminator: false</c> or where the end of the data
    /// (cut at a reserve or a tlv entry's end) ended an <c>end_terminates</c> text.</remarks>
    internal static (int Span, int Consumed) TerminatedSpan(SchemaField field, DecodeContext ctx)
    {
        for (int i = ctx.Offset; i < ctx.Data.Length; i++)
            if (ctx.Data[i] == field.Terminator) return (i - ctx.Offset, field.KeepTerminator ? 0 : 1);
        if (field.EndTerminates) return (Math.Max(ctx.Data.Length - ctx.Offset, 0), 0);
        throw BufferUnderflowException.Text(
            $"no terminator byte 0x{field.Terminator:X2} before the end of the payload (PS-593)");
    }

    /// <summary>A plain scalar YAML 1.2's core schema reads as a boolean, else null.</summary>
    internal static bool? BooleanOf(YamlNode node)
    {
        if (node is not YamlScalarNode { Style: ScalarStyle.Plain } s) return null;
        return s.Value switch
        {
            "true" or "True" or "TRUE" => true,
            "false" or "False" or "FALSE" => false,
            _ => null,
        };
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
    /// <summary>
    /// PS-624 (CR-2026-190): the index of the first byte of the first sequence that is not
    /// well-formed UTF-8 (RFC 3629), or -1; the position the reference's
    /// UnicodeDecodeError.start gives.
    /// </summary>
    internal static int FirstBadUtf8(byte[] b)
    {
        int i = 0;
        while (i < b.Length)
        {
            int c = b[i];
            if (c < 0x80) { i++; continue; }
            int len, lo = 0x80, hi = 0xBF;
            if (c >= 0xC2 && c <= 0xDF) len = 1;
            else if (c >= 0xE0 && c <= 0xEF) { len = 2; if (c == 0xE0) lo = 0xA0; if (c == 0xED) hi = 0x9F; }
            else if (c >= 0xF0 && c <= 0xF4) { len = 3; if (c == 0xF0) lo = 0x90; if (c == 0xF4) hi = 0x8F; }
            else return i;
            for (int k = 1; k <= len; k++)
            {
                if (i + k >= b.Length) return i;
                int d = b[i + k];
                if (d < (k == 1 ? lo : 0x80) || d > (k == 1 ? hi : 0xBF)) return i;
            }
            i += len + 1;
        }
        return -1;
    }

    /// <summary>
    /// PS-624: the bytes the byte rules left (<paramref name="kept"/>, one ISO 8859-1
    /// character per byte) decoded as UTF-8; a sequence that is not well formed is an error
    /// naming the field and the position of its first byte within the text.
    /// </summary>
    internal static string DecodeUtf8(string? name, string kept)
    {
        var raw = new byte[kept.Length];
        for (int i = 0; i < kept.Length; i++) raw[i] = (byte)kept[i];
        int bad = FirstBadUtf8(raw);
        if (bad >= 0)
            throw new FieldErrorException($"Field '{name}': the bytes are not well-formed UTF-8 "
                + $"from byte {bad} of the text (PS-624)");
        return System.Text.Encoding.UTF8.GetString(raw);
    }

    /// <summary>
    /// PS-625: a <c>charset: utf-8</c> value written as UTF-8, under the terminator,
    /// <c>nul: stop</c> and length rules of Helpers.EncodeAscii, with no PS-537 warning;
    /// <c>length</c> counts bytes.
    /// </summary>
    internal static byte[] EncodeUtf8(string? name, string text, int? length, string? nul,
        int? terminator, bool writeTerminator)
    {
        byte[] raw;
        try
        {
            raw = new System.Text.UTF8Encoding(false, true).GetBytes(text);
        }
        catch (System.Text.EncoderFallbackException)
        {
            throw new FieldErrorException($"Field '{name}': an ascii value holds a lone surrogate, "
                + "which has no UTF-8 form (PS-625)");
        }
        if (terminator is int term && raw.Any(b => b == term))                  // PS-595
            throw new FieldErrorException($"Field '{name}': the value holds its terminator byte "
                + $"0x{term:X2} (PS-595)");
        if (nul == "stop" && raw.Contains((byte)0))                             // PS-595
            throw new FieldErrorException($"Field '{name}': a nul: stop value holds a 0x00 byte (PS-595)");
        if (terminator != null) length = null;                                 // PS-593: no width
        if (!writeTerminator) terminator = null;
        if (length is int fixedLength && raw.Length > fixedLength)
            throw new FieldErrorException($"Field '{name}': an ascii value of {raw.Length} bytes "
                + $"is longer than its length of {fixedLength} (PS-537, PS-625)");
        var output = new byte[length ?? raw.Length + (terminator != null ? 1 : 0)];
        raw.CopyTo(output, 0);
        if (terminator is int endByte) output[raw.Length] = (byte)endByte;     // PS-595
        return output;
    }

    /// <summary>PS-624: <c>charset</c> is <c>utf-8</c>, on an ascii field; the prototype's text.</summary>
    internal static void CheckCharset(YamlMappingNode fm)
    {
        if (!fm.Children.TryGetValue(new YamlScalarNode("type"), out var typeNode)) return;
        if (!fm.Children.TryGetValue(new YamlScalarNode("charset"), out var cs)) return;
        if ((typeNode as YamlScalarNode)?.Value == "ascii" && cs is YamlScalarNode sc && sc.Value == "utf-8")
            return;
        var name = fm.Children.TryGetValue(new YamlScalarNode("name"), out var n) ? (n as YamlScalarNode)?.Value : "None";
        throw new InvalidOperationException($"Field '{name}': charset is utf-8, on an ascii field (PS-624)");
    }

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
        // PS-619 (CR-2026-188): both keys need a terminator and are true or false.
        bool hasConsume = fm.Children.TryGetValue(new YamlScalarNode("consume_terminator"), out var consume);
        bool hasEnd = fm.Children.TryGetValue(new YamlScalarNode("end_terminates"), out var endT);
        if (hasConsume || hasEnd)
        {
            if (!hasTerm)
                throw new InvalidOperationException($"Field '{name}': consume_terminator and "
                    + "end_terminates belong to a field with a terminator (PS-619)");
            if (hasConsume && BooleanOf(consume!) is null)
                throw new InvalidOperationException($"Field '{name}': consume_terminator is true or false (PS-619)");
            if (hasEnd && BooleanOf(endT!) is null)
                throw new InvalidOperationException($"Field '{name}': end_terminates is true or false (PS-619)");
        }
        if (isAscii && !fm.Children.ContainsKey(new YamlScalarNode("length")) && !hasTerm)
            throw new InvalidOperationException($"Field '{name}': ascii type requires 'length' field, "
                + "or 'terminator' (PS-072)");
    }
}
