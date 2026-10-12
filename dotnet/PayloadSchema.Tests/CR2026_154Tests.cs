using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-154 (PS-566): a negative zero is reported as 0 and written as the positive-zero
/// word, for every floating-point type. The cases are shared with the reference, the
/// generated codec, Go and Java in tests/cr-2026-154-cases.yaml.
/// </summary>
public class CR2026_154Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-154-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    static IEnumerable<object[]> Section(string section)
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-154-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode(section)]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => map.Children.TryGetValue(new YamlScalarNode(key), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            yield return new object[]
            {
                Get("name"), Get("schema"), Get("input"), Get("payload"), Get("reencode"),
            };
        }
    }

    public static IEnumerable<object[]> EncodeCases() => Section("encode_cases");
    public static IEnumerable<object[]> DecodeCases() => Section("decode_cases");

    [Theory]
    [MemberData(nameof(EncodeCases))]
    public void Encode(string name, string schemaText, string input, string payload, string reencode)
    {
        var schema = SchemaParser.Parse(schemaText);
        using var doc = JsonDocument.Parse(input);
        var data = new Dictionary<string, object?>();
        foreach (var p in doc.RootElement.EnumerateObject())
            data[p.Name] = p.Value.TryGetInt64(out var l) ? l : p.Value.GetDouble();
        var result = SchemaEncoder.Encode(schema, data);
        Assert.True(result.Success, $"[{name}] {string.Join(" | ", result.Errors)}");
        Assert.Equal(payload, Convert.ToHexString(result.Payload).ToLowerInvariant());
    }

    [Theory]
    [MemberData(nameof(DecodeCases))]
    public void DecodeAndReencode(string name, string schemaText, string input, string payload, string reencode)
    {
        var schema = SchemaParser.Parse(schemaText);
        var decoded = SchemaDecoder.Decode(schema, Convert.FromHexString(payload));
        var x = Convert.ToDouble(decoded["x"]);
        Assert.True(x == 0 && !double.IsNegative(x), $"[{name}] reported {decoded["x"]}, want a positive 0");
        var result = SchemaEncoder.Encode(schema, decoded);
        Assert.True(result.Success, $"[{name}] {string.Join(" | ", result.Errors)}");
        Assert.Equal(reencode, Convert.ToHexString(result.Payload).ToLowerInvariant());
    }
}
