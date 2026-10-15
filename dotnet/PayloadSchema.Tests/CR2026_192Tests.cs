using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-192 (PS-567, PS-569, PS-615, PS-629): a sampled repeat anchored at a time field, the
/// range of a time field on encode, and `_meta.time`. The cases are shared with the reference,
/// the generated codec, Go and Java in tests/cr-2026-192-cases.yaml.
/// </summary>
public class CR2026_192Tests
{
    static YamlMappingNode Root()
    {
        var dir = AppContext.BaseDirectory;
        string? path = null;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-192-cases.yaml");
            if (File.Exists(candidate)) { path = candidate; break; }
            dir = Path.GetDirectoryName(dir);
        }
        if (path == null) throw new FileNotFoundException("tests/cr-2026-192-cases.yaml");
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
                Get("error"), Get("reports"),
            };
        }
    }

    public static IEnumerable<object[]> DecodeCases() => Section("decode");
    public static IEnumerable<object[]> EncodeCases() => Section("cases");
    public static IEnumerable<object[]> LoadCases() => Section("load");
    public static IEnumerable<object[]> MetaCases() => Section("meta");

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
        string expected, string error, string reports)
    {
        var schema = SchemaParser.Parse(schemaText);
        if (error.Length > 0)
        {
            var e = Assert.ThrowsAny<Exception>(() => SchemaDecoder.Decode(schema, Convert.FromHexString(payload)));
            Assert.True(e.Message.Contains(error), $"[{name}] expected '{error}', got: {e.Message}");
            return;
        }
        var result = SchemaDecoder.Decode(schema, Convert.FromHexString(payload));
        using var doc = JsonDocument.Parse(expected);
        Assert.Equal(JsonSerializer.Serialize(Normal(FromJson(doc.RootElement))),
            JsonSerializer.Serialize(Normal(result)));
    }

    [Theory]
    [MemberData(nameof(EncodeCases))]
    public void Encode(string name, string schemaText, string payload, string input,
        string expected, string error, string reports)
    {
        var schema = SchemaParser.Parse(schemaText);
        using var doc = JsonDocument.Parse(input);
        var data = (Dictionary<string, object?>)FromJson(doc.RootElement)!;
        var result = SchemaEncoder.Encode(schema, data);
        if (error.Length > 0)
        {
            Assert.False(result.Success, $"[{name}] encoded {Convert.ToHexString(result.Payload)}");
            Assert.True(result.Errors.Any(e => e.Contains(error)),
                $"[{name}] expected '{error}', got: {string.Join(" | ", result.Errors)}");
            return;
        }
        Assert.True(result.Success, $"[{name}] {string.Join(" | ", result.Errors)}");
        Assert.Equal(payload, Convert.ToHexString(result.Payload).ToLowerInvariant());
    }

    [Theory]
    [MemberData(nameof(LoadCases))]
    public void Load(string name, string schemaText, string payload, string input,
        string expected, string error, string reports)
    {
        var e = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(schemaText));
        Assert.True(e.Message.Contains(error), $"[{name}] expected '{error}', got: {e.Message}");
    }

    /// <summary>PS-629: a time field's `_meta` entry carries `time`; PS-568 `at` the name.</summary>
    [Theory]
    [MemberData(nameof(MetaCases))]
    public void Meta(string name, string schemaText, string payload, string input,
        string expected, string error, string reports)
    {
        var schema = SchemaParser.Parse(schemaText);
        var output = SchemaDecoder.Interpret(schema, Convert.FromHexString(payload), new InputContext());
        var meta = (Dictionary<string, object?>)output["_meta"]!;
        var fields = (Dictionary<string, object?>)meta["fields"]!;
        using var doc = JsonDocument.Parse(expected);
        foreach (var f in doc.RootElement.EnumerateObject())
        {
            var entry = (Dictionary<string, object?>)fields[f.Name]!;
            foreach (var k in f.Value.EnumerateObject())
            {
                if (k.Value.ValueKind == JsonValueKind.Null)
                    Assert.False(entry.ContainsKey(k.Name), $"[{name}] {f.Name}.{k.Name} is absent");
                else
                    Assert.Equal(JsonSerializer.Serialize(Normal(FromJson(k.Value))),
                        JsonSerializer.Serialize(Normal(entry[k.Name])));
            }
        }
    }

    /// <summary>PS-629 in the prototype's key order: time right after reports, as declared.</summary>
    [Fact]
    public void TimeFollowsReports()
    {
        var first = Section("meta").First();
        var schema = SchemaParser.Parse((string)first[1]);
        var output = SchemaDecoder.Interpret(schema, new byte[8], new InputContext());
        var entry = (Dictionary<string, object?>)((Dictionary<string, object?>)
            ((Dictionary<string, object?>)output["_meta"]!)["fields"]!)["ts"]!;
        Assert.Equal(new[] { "type", "reports", "time" }, entry.Keys.Take(3).ToArray());
        Assert.Equal(new[] { "format", "epoch", "fraction" },
            ((Dictionary<string, object?>)entry["time"]!).Keys.ToArray());
    }
}
