using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-149 (PS-552, PS-168): an encoder rounds an integer field's value - a bit range
/// included - to the nearest integer, a tie to even, on the binary64 value its arithmetic
/// produced, and rejects a value that rounds outside the field's type (a bit range's being
/// its width), naming the field and the value given. The cases are shared with the
/// reference, the generated codec, Go and Java in tests/cr-2026-149-cases.yaml.
/// </summary>
public class CR2026_149Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-149-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-149-cases.yaml");
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
    public void EveryCase(string name, string schemaText, string input, string payload, string error)
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

    /// <summary>NaN and the infinities cannot be written in a JSON input, so the shared cases omit them.</summary>
    [Fact]
    public void SpecialValuesAreTheirWords()
    {
        var schema = SchemaParser.Parse("name: t\nfields:\n  - {name: x, type: bf16}\n");
        foreach (var (v, want) in new[] { (double.PositiveInfinity, "7f80"),
                     (double.NegativeInfinity, "ff80"), (double.NaN, "7fc0") })
        {
            var result = SchemaEncoder.Encode(schema, new Dictionary<string, object?> { ["x"] = v });
            Assert.Equal(want, Convert.ToHexString(result.Payload).ToLowerInvariant());
        }
        foreach (var word in new[] { "7f80", "ff80", "7fc1" })
        {
            var decoded = SchemaDecoder.Decode(schema, Convert.FromHexString(word));
            Assert.False(decoded.ContainsKey("x"), word);
        }
    }
}
