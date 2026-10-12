using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-146 (PS-169, 0.5.5): a label two or more codes of a lookup or an enum share is
/// rejected on encode, naming the field and the codes; the code given as a number still
/// encodes, and an enum encodes a name as its code. The cases are shared with the
/// reference, the generated codec, Go and Java in tests/cr-2026-146-cases.yaml.
/// </summary>
public class CR2026_146Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-146-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-146-cases.yaml");
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
}
