using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-177 (PS-534, PS-593 to PS-595): an ascii field's `nul` mode and `terminator`, on
/// decode and encode. The cases are shared with the reference, the generated codec, Go and
/// Java in tests/cr-2026-177-cases.yaml.
/// </summary>
public class CR2026_177Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-177-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Section(string key)
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-177-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode(key)]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string k) => !map.Children.TryGetValue(new YamlScalarNode(k), out var v) ? ""
                : v is YamlMappingNode m
                    ? string.Join(";", m.Children.OrderBy(c => ((YamlScalarNode)c.Key).Value, StringComparer.Ordinal)
                        .Select(c => $"{((YamlScalarNode)c.Key).Value}={((YamlScalarNode)c.Value).Value}"))
                    : ((YamlScalarNode)v).Value ?? "";
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
            var got = string.Join(";", data.OrderBy(c => c.Key, StringComparer.Ordinal).Select(c =>
                $"{c.Key}={Convert.ToString(c.Value, System.Globalization.CultureInfo.InvariantCulture)}"));
            Assert.Equal(expected, got);
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
}
