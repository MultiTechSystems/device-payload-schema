// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-141 (PS-551): a bit range or bool that does not consume, absent from the input,
/// is derived from the fields that write its bits and writes none; where a bit is left
/// unwritten it is missing, and a supplied one must agree with the bits the frame holds.
///
/// The cases are tests/cr-2026-141-cases.yaml, read by every implementation, so this
/// binding is held to the same bytes and the same text as the reference.
/// </summary>
public class CR2026_141Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-141-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-141-cases.yaml");
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
                Get("name"), Get("schema"), Get("input"),
                map.Children.ContainsKey(new YamlScalarNode("payload")) ? Get("payload") : "",
                map.Children.ContainsKey(new YamlScalarNode("error")) ? Get("error") : "",
                map.Children.ContainsKey(new YamlScalarNode("decoded")) ? Get("decoded") : "",
            };
        }
    }

    static object? FromJson(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Object => e.EnumerateObject()
            .ToDictionary(p => p.Name, p => FromJson(p.Value)),
        JsonValueKind.Array => e.EnumerateArray().Select(FromJson).ToList(),
        JsonValueKind.String => e.GetString(),
        JsonValueKind.Number => e.TryGetInt64(out var l) ? l : e.GetDouble(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        _ => null,
    };

    [Theory]
    [MemberData(nameof(Cases))]
    public void EveryCase(string name, string schemaText, string input, string payload, string error,
        string decoded)
    {
        var schema = SchemaParser.Parse(schemaText);
        using var doc = JsonDocument.Parse(input);
        var data = (Dictionary<string, object?>)FromJson(doc.RootElement)!;
        var result = SchemaEncoder.Encode(schema, data);
        if (error.Length > 0)
        {
            Assert.True(result.Errors.Any(e => e.Contains(error)),
                $"[{name}] expected '{error}', got: {string.Join(" | ", result.Errors)}");
            // A rejected input produces no payload, not the bytes written before the error.
            Assert.Empty(result.Payload);
        }
        else
        {
            Assert.True(result.Success, $"[{name}] {string.Join(" | ", result.Errors)}");
            Assert.Equal(payload, Convert.ToHexString(result.Payload).ToLowerInvariant());
            Assert.Empty(result.Warnings);
            if (decoded.Length > 0)
            {
                using var want = JsonDocument.Parse(decoded);
                var back = SchemaDecoder.Decode(schema, result.Payload);
                var expected = (Dictionary<string, object?>)FromJson(want.RootElement)!;
                var got = back.Where(kv => !kv.Key.StartsWith("_"))
                    .ToDictionary(kv => kv.Key, kv => kv.Value);
                Assert.True(Same(expected, got),
                    $"[{name}] decoded {JsonSerializer.Serialize(got)}, expected {decoded}");
            }
        }
    }

    static bool Same(object? want, object? got)
    {
        if (want is Dictionary<string, object?> w)
        {
            if (got is not System.Collections.IDictionary g || g.Count != w.Count) return false;
            foreach (var (k, v) in w)
                if (!g.Contains(k) || !Same(v, g[k])) return false;
            return true;
        }
        if (want is bool wb) return got is bool gb && gb == wb;
        if (want is long or double)
        {
            var (ok, gv) = got is bool ? (false, 0.0) : Helpers.ToFloat64(got);
            return ok && Math.Abs(Convert.ToDouble(want) - gv) < 1e-9;
        }
        return Equals(want, got);
    }
}
