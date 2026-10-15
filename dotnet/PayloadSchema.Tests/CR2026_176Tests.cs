using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-176 (PS-361 amended, PS-590 to PS-592): a `type: string` field may declare
/// `template`, joining values decoded earlier into text. The cases are shared with the
/// reference, the generated codec, Go and Java in tests/cr-2026-176-cases.yaml.
/// </summary>
public class CR2026_176Tests
{
    static YamlMappingNode Root()
    {
        var dir = AppContext.BaseDirectory;
        string? path = null;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-176-cases.yaml");
            if (File.Exists(candidate)) { path = candidate; break; }
            dir = Path.GetDirectoryName(dir);
        }
        if (path == null) throw new FileNotFoundException("tests/cr-2026-176-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        return (YamlMappingNode)stream.Documents[0].RootNode;
    }

    static object? FromYaml(YamlNode node) => node switch
    {
        YamlMappingNode m => m.Children.ToDictionary(
            p => ((YamlScalarNode)p.Key).Value!, p => FromYaml(p.Value)),
        YamlSequenceNode s => s.Children.Select(FromYaml).ToList(),
        YamlScalarNode sc when sc.Style != YamlDotNet.Core.ScalarStyle.Plain => sc.Value,
        YamlScalarNode sc => sc.Value switch
        {
            "true" => true,
            "false" => false,
            "null" or "~" => null,
            var v when long.TryParse(v, out var l) => l,
            var v when double.TryParse(v, System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var d) => d,
            var v => v,
        },
        _ => null,
    };

    public static IEnumerable<object[]> Section(string key)
    {
        foreach (var node in ((YamlSequenceNode)Root().Children[new YamlScalarNode(key)]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string k) => map.Children.TryGetValue(new YamlScalarNode(k), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            string expected = map.Children.TryGetValue(new YamlScalarNode("expected"), out var e)
                ? JsonSerializer.Serialize(FromYaml(e)) : "";
            yield return new object[]
            {
                Get("name"), Get("schema"), Get("payload"), Get("input"), expected,
                Get("error"), Get("warning"),
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

    /// <summary>A decoded value in a form two decodes compare by: every number as a double.</summary>
    static object? Normal(object? v) => v switch
    {
        Dictionary<string, object?> d => d.Where(p => !p.Key.StartsWith('_'))
            .OrderBy(p => p.Key, StringComparer.Ordinal)
            .ToDictionary(p => p.Key, p => Normal(p.Value)),
        System.Collections.IList l => l.Cast<object?>().Select(Normal).ToList(),
        string or bool or null => v,
        _ => Convert.ToDouble(v, System.Globalization.CultureInfo.InvariantCulture),
    };

    [Theory]
    [MemberData(nameof(DecodeCases))]
    public void Decode(string name, string schemaText, string payload, string input,
        string expected, string error, string warning)
    {
        var schema = SchemaParser.Parse(schemaText);
        var result = SchemaDecoder.Decode(schema, Convert.FromHexString(payload));
        using var doc = JsonDocument.Parse(expected);
        Assert.Equal(JsonSerializer.Serialize(Normal(FromJson(doc.RootElement))),
            JsonSerializer.Serialize(Normal(result)));
        var warnings = result.TryGetValue("_warnings", out var w) && w is List<string> list
            ? list : new List<string>();
        if (warning.Length > 0)
            Assert.True(warnings.Any(x => x.Contains(warning)),
                $"[{name}] expected '{warning}', got: {string.Join(" | ", warnings)}");
        else
            Assert.DoesNotContain(warnings, x => x.Contains("PS-591"));
    }

    [Theory]
    [MemberData(nameof(EncodeCases))]
    public void Encode(string name, string schemaText, string payload, string input,
        string expected, string error, string warning)
    {
        var schema = SchemaParser.Parse(schemaText);
        using var doc = JsonDocument.Parse(input);
        var data = (Dictionary<string, object?>)FromJson(doc.RootElement)!;
        var result = SchemaEncoder.Encode(schema, data);
        Assert.True(result.Success, $"[{name}] {string.Join(" | ", result.Errors)}");
        Assert.Equal(payload, Convert.ToHexString(result.Payload).ToLowerInvariant());
    }

    [Theory]
    [MemberData(nameof(LoadCases))]
    public void Load(string name, string schemaText, string payload, string input,
        string expected, string error, string warning)
    {
        var e = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(schemaText));
        Assert.True(e.Message.Contains(error), $"[{name}] expected '{error}', got: {e.Message}");
    }
}
