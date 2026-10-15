// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Text.RegularExpressions;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-161 (PS-570, PS-571): <c>_meta</c> carries each entry's reported type as
/// <c>reports</c>, and a lookup's labels are of one type. <c>ReportsOf</c>, <c>LookupLabels</c>,
/// <c>JsonKind</c> and <c>CheckLookupTypes</c> mirror the functions of the same purpose in
/// tools/schema_interpreter.py, whose wording and rules are the spec prototype's
/// (<c>reports_of</c>, <c>check_lookup_types</c>).
/// </summary>
public static partial class Meta
{
    static readonly Regex ReportsBitRange = new(@"^[us]\d+\[\d+:\d+\]$", RegexOptions.Compiled);
    static readonly Regex ReportsIntegerType = new(
        @"^(?:[usi]|uint|int)(?:8|16|24|32|64)$|^(?:u32le16|s32le16|u32be16le|s32be16le)$", RegexOptions.Compiled);
    static readonly HashSet<string> ReportsFloats = new()
    {
        "number", "f16", "bf16", "f32", "f64", "f32le16", "f32be16le", "uflt16", "sflt16", "sflt24", "udec", "sdec",
    };

    /// <summary>A lookup's labels, and its <c>default</c> where that is not a <c>${value}</c> template.</summary>
    public static List<object?> LookupLabels(object? lookup)
    {
        var labels = new List<object?>();
        if (lookup is List<object?> list)
            labels.AddRange(list);
        else if (lookup is Dictionary<string, object?> map)
            foreach (var kv in map)
            {
                if (kv.Key == "default" && kv.Value is string text && text.Contains(Wave5.ValueToken))
                    continue;
                labels.Add(kv.Value);
            }
        return labels;
    }

    /// <summary>The JSON type of a lookup label: boolean, integer, number or string.</summary>
    public static string JsonKind(object? v) => v switch
    {
        bool => "boolean",
        long or int => "integer",
        double d => d == Math.Floor(d) && !double.IsInfinity(d) ? "integer" : "number",
        _ => "string",
    };

    /// <summary>PS-570's reports_of; null where nothing declares one.</summary>
    public static string? ReportsOf(YamlMappingNode field)
    {
        if (Value(field, "_reports") is string forced) return forced;      // a match discriminator
        if (Get(field, "tlv") is YamlMappingNode && !Truthy(Value(field, "type"))) return "array"; // channels
        if (Has(field, "lookup") && Value(field, "lookup") is { } lookup)
        {
            if (lookup is Dictionary<string, object?> m && m.GetValueOrDefault("default") is string d
                && d.Contains(Wave5.ValueToken))
                return "string";
            var kinds = new SortedSet<string>(StringComparer.Ordinal);
            foreach (var v in LookupLabels(lookup)) kinds.Add(JsonKind(v));
            if (kinds.SetEquals(new[] { "integer", "number" })) return "number";
            return kinds.Count == 1 ? kinds.First() : null;
        }
        if (Has(field, "bit_labels") && Value(field, "bit_labels") != null)       // PS-616 (CR-2026-187)
            return Has(field, "join") ? "string" : "array";
        var declared = Value(field, "type");
        var t = declared == null ? "" : Convert.ToString(declared, System.Globalization.CultureInfo.InvariantCulture)!.ToLowerInvariant();
        var hasValue = Has(field, "value");
        if (t == "repeat") return "array";
        if (t == "object") return "object";
        if ((t == "string" || t == "number" || t == "boolean") && hasValue) return t;
        if (t == "bytes" && Value(field, "format") is "array") return "array";
        if (t == "bool" || t == "boolean") return "boolean";
        if (t == "ascii") return Truthy(Value(field, "numeral")) ? "integer" : "string";
        if (t is "string" or "bitfield_string" or "enum" or "bytes" or "hex" or "base64") return "string";
        foreach (var k in new[] { "mult", "div", "add", "polynomial", "transform" })
            if (Has(field, k)) return "number";
        if (t == "integer") return "integer";
        if (ReportsFloats.Contains(t)) return "number";
        if (ReportsBitRange.IsMatch(t) || ReportsIntegerType.IsMatch(t)) return "integer";
        return null;
    }

    /// <summary>Throws the first PS-571 violation in the header, fields, ports and definitions.</summary>
    public static void CheckLookupTypes(YamlMappingNode root)
    {
        foreach (var key in new[] { "header", "fields", "ports", "definitions" })
            if (Get(root, key) is { } node) WalkLookupTypes(node);
    }

    static void WalkLookupTypes(YamlNode node)
    {
        if (node is YamlSequenceNode seq)
        {
            foreach (var item in seq.Children) WalkLookupTypes(item);
        }
        else if (node is YamlMappingNode map)
        {
            if (Has(map, "lookup") && Value(map, "type") != null)
            {
                var kinds = new SortedSet<string>(StringComparer.Ordinal);
                foreach (var v in LookupLabels(Value(map, "lookup")))
                {
                    var k = JsonKind(v);
                    kinds.Add(k == "integer" ? "number" : k);
                }
                if (kinds.Count > 1)
                {
                    var name = Value(map, "name") is string s ? s : "None";
                    throw new InvalidOperationException(
                        $"Field '{name}': its lookup mixes {string.Join(", ", kinds)} labels; a lookup's labels are of one type (PS-571)");
                }
            }
            foreach (var kv in map.Children)
                if (!(kv.Key is YamlScalarNode k && k.Value == "test_vectors"))
                    WalkLookupTypes(kv.Value);
        }
    }
}
