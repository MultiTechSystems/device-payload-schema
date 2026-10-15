// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-168: a definition holds a field group, one field or a lookup table (PS-345); a
/// reference to a field may rename it and replace keys whole (PS-578); a field's `lookup`
/// may point to a table (PS-579); references resolve transitively (PS-580). The cases are
/// tests/cr-2026-168-cases.yaml, shared with the reference, Go and Java.
/// </summary>
public class CR2026_168Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-168-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-168-cases.yaml");
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
                Get("encode"), Get("encoded"), Get("fport"), Get("round_trip"),
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
        string encode, string encoded, string fport, string roundTrip)
    {
        if (error.Length > 0)
        {
            var e = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(schemaText));
            Assert.Equal(error, e.Message);
            return;
        }
        var schema = SchemaParser.Parse(schemaText);
        int port = fport.Length > 0 ? int.Parse(fport) : 0;
        var decoded = port > 0 ? SchemaDecoder.DecodeWithPort(schema, Convert.FromHexString(payload), port)
            : SchemaDecoder.Decode(schema, Convert.FromHexString(payload));
        using var doc = JsonDocument.Parse(expected);
        var values = decoded.Where(kv => kv.Key != "_quality")     // `valid_range` reports one beside the values
            .ToDictionary(kv => kv.Key, kv => kv.Value);
        Assert.Equal(Normalize(FromJson(doc.RootElement)), Normalize(values));
        if (roundTrip != "false")
        {
            var result = SchemaEncoder.EncodeWithPort(schema, decoded, port);
            Assert.True(result.Success, $"[{name}] {string.Join(" | ", result.Errors)}");
            Assert.Equal(payload, Convert.ToHexString(result.Payload).ToLowerInvariant());
        }
        if (encode.Length > 0)
        {
            using var given = JsonDocument.Parse(encode);
            var again = SchemaEncoder.EncodeWithPort(schema, (Dictionary<string, object?>)FromJson(given.RootElement)!, port);
            Assert.True(again.Success, $"[{name}] {string.Join(" | ", again.Errors)}");
            Assert.Equal(encoded, Convert.ToHexString(again.Payload).ToLowerInvariant());
        }
    }

    /// <summary>A resolved template keeps its key order: two cases equally given, the first is written (PS-563).</summary>
    [Fact]
    public void ATemplateKeepsItsCaseOrder()
    {
        var schema = SchemaParser.Parse("name: t\ndefinitions:\n  f:\n    field:\n      type: object\n"
            + "      fields:\n        - match:\n            length: 1\n            cases:\n"
            + "              2: [{name: a, type: u8}]\n              1: [{name: a, type: u8}]\n"
            + "fields:\n  - {$ref: '#/definitions/f', name: o}\n");
        var result = SchemaEncoder.Encode(schema, new Dictionary<string, object?>
            { ["o"] = new Dictionary<string, object?> { ["a"] = 5L } });
        Assert.True(result.Success, string.Join(" | ", result.Errors));
        Assert.Equal("0205", Convert.ToHexString(result.Payload).ToLowerInvariant());
    }
}
