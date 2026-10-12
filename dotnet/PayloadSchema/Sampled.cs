// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-156 (PS-567 to PS-569): <c>sampled</c> on a repeat.
///
/// <c>sampled</c> says a repeat's elements are samples of one measurement taken at a fixed
/// interval. It is an annotation: decoding and encoding ignore it. This file holds the load
/// check (PS-569) and the <c>_meta</c> entry (PS-568); both mirror <c>sampled_errors</c> and
/// <c>sampled_meta</c> in tools/schema_interpreter.py, whose wording is the spec prototype's
/// <c>check_sampled</c>. A <c>$</c> interval must name a field the repeat can see, which
/// <c>CheckReferences</c> holds to PS-539 (<c>interval</c> is one of its reference keys).
/// </summary>
public static partial class SchemaParser
{
    /// <summary>PS-568: the declaration with order and at filled in; a $name interval is {field: name}.</summary>
    public static Dictionary<string, object?> SampledMeta(object? sampled)
    {
        var map = sampled as Dictionary<string, object?> ?? new Dictionary<string, object?>();
        var interval = map.GetValueOrDefault("interval");
        if (interval is string text)
            interval = new Dictionary<string, object?> { ["field"] = text.StartsWith('$') ? text[1..] : text };
        return new Dictionary<string, object?>
        {
            ["interval"] = interval,
            ["order"] = map.ContainsKey("order") ? map["order"] : "newest_first",
            ["at"] = map.ContainsKey("at") ? map["at"] : "rx_time",
        };
    }

    /// <summary>Throws the first PS-569 violation in the header, fields, ports and definitions.</summary>
    static void CheckSampled(YamlMappingNode root)
    {
        var times = new HashSet<string>();
        if (Meta.FromYaml(NameKey(root, "metadata")) is Dictionary<string, object?> metadata
            && metadata.GetValueOrDefault("timestamps") is List<object?> list)
        {
            foreach (var item in list)
                if (item is Dictionary<string, object?> ts && ts.GetValueOrDefault("name") is string name
                    && ts.GetValueOrDefault("mode") is "unix_epoch" or "iso8601")
                    times.Add(name);
        }
        foreach (var key in new[] { "header", "fields", "ports", "definitions" })
            WalkSampled(NameKey(root, key), times);
    }

    static void WalkSampled(YamlNode? node, HashSet<string> times)
    {
        if (node is YamlSequenceNode seq)
        {
            foreach (var item in seq.Children) WalkSampled(item, times);
        }
        else if (node is YamlMappingNode map)
        {
            if (NameKey(map, "sampled") is { } sampled && NameKey(map, "type") is { } type
                && Meta.FromYaml(type) != null)
                CheckSampledField(map, sampled, times);
            foreach (var kv in map.Children)
                if (!(kv.Key is YamlScalarNode k && k.Value == "test_vectors"))
                    WalkSampled(kv.Value, times);
        }
    }

    static InvalidOperationException BadSampled(YamlMappingNode field, string why)
    {
        var name = Meta.FromYaml(NameKey(field, "name")) is string s ? s : "None";
        return new InvalidOperationException($"Field '{name}': sampled {why} (PS-569)");
    }

    static void CheckSampledField(YamlMappingNode field, YamlNode sampledNode, HashSet<string> times)
    {
        var type = Meta.FromYaml(NameKey(field, "type"));
        if (!(type is string t && t == "repeat"))
            throw BadSampled(field, $"is defined only on a repeat, not on {PlainText(type)}");
        if (Meta.FromYaml(sampledNode) is not Dictionary<string, object?> sampled || !sampled.ContainsKey("interval"))
            throw BadSampled(field, "needs an interval");
        var interval = sampled["interval"];
        var valid = interval switch
        {
            bool => false,
            long l => l > 0,
            double d => d > 0,
            string s => s.StartsWith('$'),
            Dictionary<string, object?> m => m.Count == 1 && m.GetValueOrDefault("configured") is string,
            _ => false,
        };
        if (!valid)
            throw BadSampled(field, $"interval {PyRepr(interval)} is not a number of seconds above 0, a $ reference or {{configured: name}}");
        if (sampled.TryGetValue("order", out var order) && !(order is "newest_first" or "oldest_first"))
            throw BadSampled(field, $"order {PyRepr(order)} is not newest_first or oldest_first");
        if (sampled.TryGetValue("at", out var at) && !(at is string a && (a == "rx_time" || times.Contains(a))))
            throw BadSampled(field, $"at {PyRepr(at)} is neither rx_time nor a unix_epoch or iso8601 timestamps entry");
    }

    /// <summary>What Python's str() writes for a field's type in "not on {type}".</summary>
    static string PlainText(object? v) => v switch
    {
        null => "None",
        bool b => b ? "True" : "False",
        _ => Convert.ToString(v, CultureInfo.InvariantCulture) ?? "",
    };

    /// <summary>A value as Python's repr writes it, so a message names it as the reference's does.</summary>
    static string PyRepr(object? v) => v switch
    {
        null => "None",
        string s => "'" + s + "'",
        bool b => b ? "True" : "False",
        double d => d == Math.Floor(d) && !double.IsInfinity(d) && Math.Abs(d) < 1e16
            ? ((long)d).ToString(CultureInfo.InvariantCulture) + ".0"
            : d.ToString("R", CultureInfo.InvariantCulture),
        List<object?> l => "[" + string.Join(", ", l.Select(PyRepr)) + "]",
        Dictionary<string, object?> m => "{" + string.Join(", ", m.Select(e => PyRepr(e.Key) + ": " + PyRepr(e.Value))) + "}",
        _ => Convert.ToString(v, CultureInfo.InvariantCulture) ?? "",
    };
}
