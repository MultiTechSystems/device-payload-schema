using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-179 (0.5.7, PS-470, PS-600): a <c>semantic</c> is a string, a TTN normalized path,
/// and <c>_meta</c> carries it. The cases are shared with the reference, Go and Java in
/// tests/cr-2026-179-cases.yaml; the <c>normalized</c> ones are the generated codec's and the
/// reference's alone.
/// </summary>
public class CR2026_179Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-179-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-179-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode("cases")]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => map.Children.TryGetValue(new YamlScalarNode(key), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            var meta = new List<string>();
            if (map.Children.TryGetValue(new YamlScalarNode("meta"), out var m))
                foreach (var kv in ((YamlMappingNode)m).Children)
                {
                    var value = ((YamlScalarNode)kv.Value).Value;
                    meta.Add(((YamlScalarNode)kv.Key).Value + "=" + (value == "null" ? "" : value));
                }
            if (Get("error").Length == 0 && meta.Count == 0) continue;
            yield return new object[] { Get("name"), Get("schema"), Get("payload"), string.Join("\n", meta), Get("error") };
        }
    }

    [Theory]
    [MemberData(nameof(Cases))]
    public void EveryCase(string name, string schemaText, string payload, string meta, string error)
    {
        if (error.Length > 0)
        {
            var ex = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(schemaText));
            Assert.True(ex.Message.Contains(error), $"[{name}] expected '{error}', got: {ex.Message}");
            return;
        }
        var schema = SchemaParser.Parse(schemaText);
        var output = SchemaDecoder.Interpret(schema, Convert.FromHexString(payload), new InputContext());
        var metaOut = Assert.IsType<Dictionary<string, object?>>(output["_meta"]);
        var fields = Assert.IsType<Dictionary<string, object?>>(metaOut["fields"]);
        foreach (var line in meta.Split('\n'))
        {
            var parts = line.Split('=', 2);
            var entry = Assert.IsType<Dictionary<string, object?>>(fields[parts[0]]);
            if (parts[1].Length == 0)
                Assert.False(entry.ContainsKey("semantic"), $"[{name}] {parts[0]}");
            else
                Assert.Equal(parts[1], entry["semantic"]);
        }
    }
}
