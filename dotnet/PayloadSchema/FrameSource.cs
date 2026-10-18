// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-196 (PS-635 to PS-637): a <c>type: number</c> field declaring <c>source</c>
/// reports a value of the frame, reading no bytes: <c>$fPort</c>, <c>$payloadLength</c> or
/// <c>$remaining</c> (the bytes from its position to the end of the data, which a tlv entry
/// cuts at its end, as <c>match: {remaining: true}</c> counts). Its modifiers apply once and
/// a reference binds the value. With no FPort a <c>$fPort</c> field is omitted with a warning
/// (PS-636). An encoder writes nothing and checks a given value against the finished frame
/// (PS-637). Mirrors frame_source_errors, _frame_source and _encode_frame_source in
/// tools/schema_interpreter.py.
/// </summary>
internal static class FrameSource
{
    internal static readonly string[] Sources = { "$fPort", "$payloadLength", "$remaining" };

    internal static string LoadMessage(string? name) =>
        $"Field '{name ?? "None"}': source is one of {string.Join(", ", Sources)}, on a type: "
        + "number field that computes nothing else (PS-635)";

    /// <summary>PS-635 at load: the prototype's check_frame_sources, on one field mapping.</summary>
    internal static void Check(YamlMappingNode fm)
    {
        if (!fm.Children.TryGetValue(new YamlScalarNode("type"), out var typeNode)) return;
        if (!fm.Children.TryGetValue(new YamlScalarNode("source"), out var srcNode)) return;
        var type = ((typeNode as YamlScalarNode)?.Value ?? "").ToLowerInvariant();
        var source = (srcNode as YamlScalarNode)?.Value;
        bool clash = new[] { "value", "compute", "ref", "polynomial" }
            .Any(k => fm.Children.ContainsKey(new YamlScalarNode(k)));
        if (fm.Children.TryGetValue(new YamlScalarNode("time"), out var time)
            && time is YamlMappingNode tm && tm.Children.ContainsKey(new YamlScalarNode("parts")))
            clash = true;
        if (type is not ("number" or "integer") || source == null || !Sources.Contains(source) || clash)
        {
            var name = fm.Children.TryGetValue(new YamlScalarNode("name"), out var n)
                ? (n as YamlScalarNode)?.Value : null;
            throw new InvalidOperationException(LoadMessage(name));
        }
    }

    /// <summary>PS-635, PS-636: the integer the field's source names, or null (omitted, with
    /// the warning) where it is <c>$fPort</c> and no FPort was given.</summary>
    internal static long? Raw(SchemaField field, DecodeContext ctx)
    {
        long? raw = field.Source switch
        {
            "$remaining" => Math.Max(0, ctx.Remaining),
            "$payloadLength" => ctx.PayloadLength,
            _ => ctx.FrameFPort,
        };
        if (raw == null)
            ctx.Warnings.Add($"Field '{field.Name}': no FPort was given, so it is omitted (PS-636)");
        return raw;
    }

    /// <summary>PS-637's rejection of a given value the finished frame does not bear out.</summary>
    internal static string Mismatch(SchemaField field, string given, object? found) =>
        $"Field '{field.Name}' is given {given}, but the frame gives "
        + (found == null ? "no FPort" : SchemaEncoder.JsNumber(found)) + " (PS-637)";
}
