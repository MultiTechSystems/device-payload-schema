using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-178 (0.5.7, PS-178, PS-596): <c>_meta</c> entries carry <c>display_unit</c>, the
/// field's <c>unit</c> as declared, beside <c>unit</c>.
/// The cases are shared with the reference, Go and Java in tests/cr-2026-178-cases.yaml.
/// </summary>
public class CR2026_178Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-178-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-178-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode("cases")]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => ((YamlScalarNode)map.Children[new YamlScalarNode(key)]).Value ?? "";
            yield return new object[] { Get("name"), Get("schema"), Get("payload"), Get("path"), Get("unit"), Get("display_unit") };
        }
    }

    [Theory]
    [MemberData(nameof(Cases))]
    public void EveryCase(string name, string schemaText, string payload, string path, string unit, string displayUnit)
    {
        var schema = SchemaParser.Parse(schemaText);
        var output = SchemaDecoder.Interpret(schema, Convert.FromHexString(payload), new InputContext());
        var meta = Assert.IsType<Dictionary<string, object?>>(output["_meta"]);
        var entry = Assert.IsType<Dictionary<string, object?>>(meta["fields"]);
        foreach (var key in path.Split('.'))
            entry = Assert.IsType<Dictionary<string, object?>>(entry[key]);
        foreach (var (key, want) in new[] { ("unit", unit), ("display_unit", displayUnit) })
        {
            if (want == "absent") Assert.False(entry.ContainsKey(key), $"[{name}] {key}");
            else Assert.Equal(want, entry[key]);
        }
    }
}
