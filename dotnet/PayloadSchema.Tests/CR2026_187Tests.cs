using System.Globalization;
using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-187 (PS-616 to PS-618): bit_labels reports the labels of a bitmask's set bits, and
/// an encoder sets the bits of the labels it is given. The cases are shared with the
/// reference, the generated codec, Go and Java in tests/cr-2026-187-cases.yaml.
/// </summary>
public class CR2026_187Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-187-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    static YamlMappingNode Root()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-187-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        return (YamlMappingNode)stream.Documents[0].RootNode;
    }

    /// <summary>name, schema, payload, input, expected (canonical text), error, warnings, reports.</summary>
    public static IEnumerable<object?[]> Section(string key)
    {
        foreach (var node in ((YamlSequenceNode)Root().Children[new YamlScalarNode(key)]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string k) => map.Children.TryGetValue(new YamlScalarNode(k), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            string? expected = map.Children.TryGetValue(new YamlScalarNode("expected"), out var e)
                ? Norm(FromYaml(e)) : null;
            var warnings = map.Children.TryGetValue(new YamlScalarNode("warnings"), out var w)
                ? string.Join("\n", ((YamlSequenceNode)w).Children.Select(x => ((YamlScalarNode)x).Value))
                : "";
            yield return new object?[]
            {
                Get("name"), Get("schema"), Get("payload"), Get("input"), expected,
                Get("error"), warnings, Get("reports"),
            };
        }
    }

    public static IEnumerable<object?[]> DecodeCases() => Section("decode");
    public static IEnumerable<object?[]> EncodeCases() => Section("cases");
    public static IEnumerable<object?[]> MetaCases() => Section("meta");
    public static IEnumerable<object?[]> LoadCases() => Section("load");

    /// <summary>A YAML value: a quoted scalar is text, a plain one a number where it reads as one.</summary>
    static object? FromYaml(YamlNode node) => node switch
    {
        YamlMappingNode m => m.Children.ToDictionary(kv => ((YamlScalarNode)kv.Key).Value ?? "",
            kv => FromYaml(kv.Value)),
        YamlSequenceNode s => s.Children.Select(FromYaml).ToList(),
        YamlScalarNode { Style: YamlDotNet.Core.ScalarStyle.Plain } p
            when double.TryParse(p.Value, NumberStyles.Float, CultureInfo.InvariantCulture, out var d) => d,
        YamlScalarNode sc => sc.Value,
        _ => null,
    };

    /// <summary>A canonical text: keys sorted, every number as a double.</summary>
    static string Norm(object? v) => v switch
    {
        null => "null",
        string s => "\"" + s + "\"",
        bool b => b ? "true" : "false",
        System.Collections.IDictionary m => "{" + string.Concat(m.Keys.Cast<object>()
            .Select(k => k.ToString()!).OrderBy(k => k, StringComparer.Ordinal)
            .Select(k => k + "=" + Norm(m[k]) + ",")) + "}",
        System.Collections.IList l => "[" + string.Concat(l.Cast<object?>().Select(x => Norm(x) + ",")) + "]",
        _ => Convert.ToDouble(v, CultureInfo.InvariantCulture).ToString("R", CultureInfo.InvariantCulture),
    };

    static object? FromJson(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Object => e.EnumerateObject().ToDictionary(p => p.Name, p => FromJson(p.Value)),
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
        string? expected, string error, string warnings, string reports)
    {
        var schema = SchemaParser.Parse(schemaText);
        var data = SchemaDecoder.Decode(schema, Convert.FromHexString(payload));
        var got = data.GetValueOrDefault("_warnings") as List<string> ?? new List<string>();
        var reported = data.Where(kv => kv.Key is not ("_warnings" or "_quality"))
            .ToDictionary(kv => kv.Key, kv => kv.Value);
        Assert.Equal(expected, Norm(reported));
        var want = warnings.Length > 0 ? warnings.Split('\n') : Array.Empty<string>();
        foreach (var w in want)
            Assert.True(got.Contains(w), $"[{name}] expected warning '{w}', got: {string.Join(" | ", got)}");
        if (want.Length == 0)
            Assert.Empty(got);
    }

    [Theory]
    [MemberData(nameof(EncodeCases))]
    public void Encode(string name, string schemaText, string payload, string input,
        string? expected, string error, string warnings, string reports)
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
    [MemberData(nameof(MetaCases))]
    public void MetaReports(string name, string schemaText, string payload, string input,
        string? expected, string error, string warnings, string reports)
    {
        var schema = SchemaParser.Parse(schemaText);
        var output = SchemaDecoder.Interpret(schema, Convert.FromHexString(payload), new InputContext());
        var meta = Assert.IsType<Dictionary<string, object?>>(output["_meta"]);
        var fields = Assert.IsType<Dictionary<string, object?>>(meta["fields"]);
        var entry = Assert.IsType<Dictionary<string, object?>>(fields["f"]);
        Assert.True(reports == (string?)entry["reports"], $"[{name}] reports {entry["reports"]}");
    }

    [Theory]
    [MemberData(nameof(LoadCases))]
    public void Load(string name, string schemaText, string payload, string input,
        string? expected, string error, string warnings, string reports)
    {
        var e = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(schemaText));
        Assert.True(e.Message.Contains(error), $"[{name}] expected '{error}', got: {e.Message}");
    }
}
