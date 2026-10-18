using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-198 (PS-621 to PS-623 amended, PS-639, PS-640): packed records of several
/// fields, a bit offset of any size or by reference, and reading in place. The cases are shared with the reference, the
/// generated codec, Go and Java in tests/cr-2026-198-cases.yaml.
/// </summary>
public class CR2026_198Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-198-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Section(string key)
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-198-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode(key)]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string k) => map.Children.TryGetValue(new YamlScalarNode(k), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            yield return new object[]
            {
                Get("name"), Get("schema"), Get("payload"), Get("input"), Get("expected"),
                Get("error"), map.Children.ContainsKey(new YamlScalarNode("expected")),
            };
        }
    }

    public static IEnumerable<object[]> DecodeCases() => Section("decode");
    public static IEnumerable<object[]> EncodeCases() => Section("cases");
    public static IEnumerable<object[]> LoadCases() => Section("load");

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
    [MemberData(nameof(DecodeCases))]
    public void Decode(string name, string schemaText, string payload, string input,
        string expected, string error, bool hasExpected)
    {
        var schema = SchemaParser.Parse(schemaText);
        var bytes = Convert.FromHexString(payload);
        if (error.Length > 0)
        {
            var e = Assert.ThrowsAny<Exception>(() => SchemaDecoder.Decode(schema, bytes));
            Assert.True(e.Message.Contains(error), $"[{name}] expected '{error}', got: {e.Message}");
        }
        else
        {
            var data = SchemaDecoder.Decode(schema, bytes);
            using var want = JsonDocument.Parse(expected);
            Assert.True(Same(FromJson(want.RootElement), data),
                $"[{name}] expected {expected}, got {JsonSerializer.Serialize(data)}");
        }
    }

    [Theory]
    [MemberData(nameof(EncodeCases))]
    public void Encode(string name, string schemaText, string payload, string input,
        string expected, string error, bool hasExpected)
    {
        var schema = SchemaParser.Parse(schemaText);
        using var doc = JsonDocument.Parse(input);
        var data = (Dictionary<string, object?>)FromJson(doc.RootElement)!;
        var result = SchemaEncoder.Encode(schema, data);
        if (error.Length > 0)
        {
            Assert.True(result.Errors.Any(e => e.Contains(error)),
                $"[{name}] expected '{error}', got: {string.Join(" | ", result.Errors)}");
            Assert.Empty(result.Payload);
        }
        else
        {
            Assert.True(result.Success, $"[{name}] {string.Join(" | ", result.Errors)}");
            Assert.Equal(payload, Convert.ToHexString(result.Payload).ToLowerInvariant());
        }
    }

    [Theory]
    [MemberData(nameof(LoadCases))]
    public void Load(string name, string schemaText, string payload, string input,
        string expected, string error, bool hasExpected)
    {
        var e = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(schemaText));
        Assert.True(e.Message.Contains(error), $"[{name}] expected '{error}', got: {e.Message}");
    }

    /// <summary>JSON equality: numbers by value, lists and maps member by member.</summary>
    static bool Same(object? want, object? got)
    {
        if (want == null || got == null) return want == null && got == null;
        if (want is string ws) return got is string gs && ws == gs;
        if (want is bool wb) return got is bool gb && wb == gb;
        if (want is Dictionary<string, object?> wm)
            return got is IDictionary<string, object?> gm && wm.Count == gm.Count
                && wm.All(kv => gm.TryGetValue(kv.Key, out var g) && Same(kv.Value, g));
        if (want is List<object?> wl)
            return got is System.Collections.IList gl && wl.Count == gl.Count
                && wl.Select((w, i) => Same(w, gl[i])).All(x => x);
        return Convert.ToDouble(want) == Convert.ToDouble(got);
    }
}
