using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-172 (PS-119, PS-584): compute <c>pow</c>; a result that is not a finite real number
/// omits the field. The cases are shared with the reference, the generated codec, Go and Java
/// in tests/cr-2026-172-cases.yaml.
/// </summary>
public class CR2026_172Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-172-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-172-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode("decode_cases")]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => ((YamlScalarNode)map.Children[new YamlScalarNode(key)]).Value ?? "";
            yield return new object[] { Get("name"), Get("schema"), Get("payload"), Get("expected") };
        }
    }

    [Theory]
    [MemberData(nameof(Cases))]
    public void Decode(string name, string schemaText, string payload, string expected)
    {
        var schema = SchemaParser.Parse(schemaText);
        var got = SchemaDecoder.Decode(schema, Convert.FromHexString(payload));
        using var doc = JsonDocument.Parse(expected);
        var want = doc.RootElement.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetDouble());
        Assert.Equal(want.Keys.OrderBy(k => k), got.Keys.OrderBy(k => k));
        foreach (var (key, w) in want)
            Assert.True(Math.Abs(Convert.ToDouble(got[key]) - w) <= 1e-9 * Math.Max(1, Math.Abs(w)),
                $"[{name}] {key} = {got[key]}, want {w}");
    }
}
