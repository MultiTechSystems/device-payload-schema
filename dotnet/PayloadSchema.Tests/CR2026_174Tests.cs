using System.Globalization;
using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-174 (PS-585 to PS-587): a <c>checksum</c> on an unsigned integer field is verified
/// on decode, with an error or a warning on a mismatch, and computed on encode. The cases
/// are shared with the reference, the generated codec, Go and Java in
/// tests/cr-2026-174-cases.yaml.
/// </summary>
public class CR2026_174Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-174-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    static YamlMappingNode Root()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-174-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        return (YamlMappingNode)stream.Documents[0].RootNode;
    }

    /// <summary>name, schema, payload, input, expected (canonical text, or null), error, warning.</summary>
    public static IEnumerable<object?[]> Section(string key)
    {
        foreach (var node in ((YamlSequenceNode)Root().Children[new YamlScalarNode(key)]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string k) => map.Children.TryGetValue(new YamlScalarNode(k), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            string? expected = map.Children.TryGetValue(new YamlScalarNode("expected"), out var e)
                ? Norm(FromYaml(e)) : null;
            yield return new object?[]
            {
                Get("name"), Get("schema"), Get("payload"), Get("input"), expected,
                Get("error"), Get("warning"),
            };
        }
    }

    public static IEnumerable<object?[]> DecodeCases() => Section("decode");
    public static IEnumerable<object?[]> EncodeCases() => Section("cases");
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
        string? expected, string error, string warning)
    {
        var schema = SchemaParser.Parse(schemaText);
        var bytes = Convert.FromHexString(payload);
        if (error.Length > 0)
        {
            var e = Assert.ThrowsAny<Exception>(() => SchemaDecoder.Decode(schema, bytes));
            Assert.True(e.Message.Contains(error), $"[{name}] expected '{error}', got: {e.Message}");
            return;
        }
        var data = SchemaDecoder.Decode(schema, bytes);
        var warnings = data.GetValueOrDefault("_warnings") as List<string> ?? new List<string>();
        var reported = data.Where(kv => kv.Key is not ("_warnings" or "_quality"))
            .ToDictionary(kv => kv.Key, kv => kv.Value);
        Assert.Equal(expected, Norm(reported));
        if (warning.Length > 0)
            Assert.True(warnings.Any(w => w.Contains(warning)),
                $"[{name}] expected warning '{warning}', got: {string.Join(" | ", warnings)}");
    }

    [Theory]
    [MemberData(nameof(EncodeCases))]
    public void Encode(string name, string schemaText, string payload, string input,
        string? expected, string error, string warning)
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
        string? expected, string error, string warning)
    {
        var e = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(schemaText));
        Assert.True(e.Message.Contains(error), $"[{name}] expected '{error}', got: {e.Message}");
    }

    /// <summary>The catalogue check values over the ASCII bytes 123456789.</summary>
    [Theory]
    [InlineData("sum8", 0xDD)]
    [InlineData("xor8", 0x31)]
    [InlineData("crc8/smbus", 0xF4)]
    [InlineData("crc16/kermit", 0x2189)]
    public void CatalogueCheckValues(string algo, int check)
    {
        Assert.Equal((ulong)check, Checksum.Compute(algo, "123456789"u8.ToArray(), 0));
    }
}
