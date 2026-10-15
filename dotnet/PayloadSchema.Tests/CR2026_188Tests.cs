using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-188 (PS-593, PS-619, PS-620): a terminator left unread (`consume_terminator:
/// false`) and text the end of the data ends (`end_terminates: true`), on decode and encode. The cases are shared with the reference, the generated codec, Go and
/// Java in tests/cr-2026-188-cases.yaml.
/// </summary>
public class CR2026_188Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-188-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Section(string key)
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-188-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode(key)]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string k) => !map.Children.TryGetValue(new YamlScalarNode(k), out var v) ? ""
                : RenderYaml(v);
            yield return new object[]
            {
                Get("name"), Get("schema"), Get("payload"), Get("input"), Get("expected"),
                Get("error"), map.Children.ContainsKey(new YamlScalarNode("expected")),
            };
        }
    }

    // One rendering for an expected YAML value and a decoded one: a mapping's entries by
    // key, "k=v" joined with ";", a list's items in brackets, a scalar as its text.
    static string RenderYaml(YamlNode v) => v switch
    {
        YamlMappingNode m => "{" + string.Join(";", m.Children
            .OrderBy(c => ((YamlScalarNode)c.Key).Value, StringComparer.Ordinal)
            .Select(c => $"{((YamlScalarNode)c.Key).Value}={RenderYaml(c.Value)}")) + "}",
        YamlSequenceNode q => "[" + string.Join(",", q.Children.Select(RenderYaml)) + "]",
        _ => ((YamlScalarNode)v).Value ?? "",
    };

    static string RenderValue(object? v) => v switch
    {
        IDictionary<string, object?> m => "{" + string.Join(";", m
            .OrderBy(c => c.Key, StringComparer.Ordinal)
            .Select(c => $"{c.Key}={RenderValue(c.Value)}")) + "}",
        string text => text,
        System.Collections.IEnumerable list => "[" + string.Join(",",
            list.Cast<object?>().Select(RenderValue)) + "]",
        _ => Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture) ?? "",
    };

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
            Assert.Equal(expected, RenderValue(data));
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
