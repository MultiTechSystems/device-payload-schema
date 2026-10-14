// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-171 (PS-390): `ties: up` rounds an exact tie toward +infinity, as Math.round does,
/// so a negative tie goes toward zero. The cases are tests/cr-2026-171-cases.yaml, shared with
/// the reference, Go and Java.
/// </summary>
public class CR2026_171Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-171-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-171-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode("cases")]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => map.Children.TryGetValue(new YamlScalarNode(key), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            yield return new object[]
            {
                Get("name"), Get("schema"), Get("error"), Get("payload"), Get("expected"),
                Get("encode"), Get("encoded"),
            };
        }
    }

    /// <summary>Numbers compared by value, so a decoded long equals a parsed double.</summary>
    static object? Normalize(object? v) => v switch
    {
        Dictionary<string, object?> d => d.OrderBy(kv => kv.Key, StringComparer.Ordinal)
            .Select(kv => $"{kv.Key}={Normalize(kv.Value)}").Aggregate("{", (a, b) => a + b + ";") + "}",
        System.Collections.IList l => l.Cast<object?>().Select(Normalize)
            .Aggregate("[", (a, b) => a + b + ",") + "]",
        bool b => b ? "true" : "false",
        string s => "\"" + s + "\"",
        null => "null",
        _ => Convert.ToDouble(v).ToString(System.Globalization.CultureInfo.InvariantCulture),
    };

    static object? FromJson(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Object => e.EnumerateObject()
            .ToDictionary(p => p.Name, p => FromJson(p.Value)),
        JsonValueKind.Array => e.EnumerateArray().Select(FromJson).ToList(),
        JsonValueKind.String => e.GetString(),
        JsonValueKind.Number => e.GetDouble(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        _ => null,
    };

    [Theory]
    [MemberData(nameof(Cases))]
    public void EveryCase(string name, string schemaText, string error, string payload, string expected,
        string encode, string encoded)
    {
        if (error.Length > 0)
        {
            var e = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(schemaText));
            Assert.Contains(error, e.Message);
            return;
        }
        var schema = SchemaParser.Parse(schemaText);
        var decoded = SchemaDecoder.Decode(schema, Convert.FromHexString(payload.Replace(" ", "")));
        using var doc = JsonDocument.Parse(expected);
        Assert.Equal(Normalize(FromJson(doc.RootElement)), Normalize(decoded));
    }
}
