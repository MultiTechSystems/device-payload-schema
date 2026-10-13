using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-161 (0.5.6, PS-570, PS-571): <c>_meta</c> entries carry <c>reports</c>, and a
/// lookup's labels are of one type.
/// The cases are shared with the reference, Go and Java in tests/cr-2026-161-cases.yaml.
/// </summary>
public class CR2026_161Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-161-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-161-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode("cases")]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => map.Children.TryGetValue(new YamlScalarNode(key), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            yield return new object[] { Get("name"), Get("schema"), Get("payload"), Get("reports"), Get("element_reports"), Get("error") };
        }
    }

    [Theory]
    [MemberData(nameof(Cases))]
    public void EveryCase(string name, string schemaText, string payload, string reports, string elementReports, string error)
    {
        if (error.Length > 0)
        {
            var ex = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(schemaText));
            Assert.True(ex.Message.Contains(error), $"[{name}] expected '{error}', got: {ex.Message}");
            return;
        }
        var schema = SchemaParser.Parse(schemaText);
        var output = SchemaDecoder.Interpret(schema, Convert.FromHexString(payload), new InputContext());
        var meta = Assert.IsType<Dictionary<string, object?>>(output["_meta"]);
        var fields = Assert.IsType<Dictionary<string, object?>>(meta["fields"]);
        var entry = Assert.IsType<Dictionary<string, object?>>(fields["f"]);
        Assert.Equal(reports, entry["reports"]);
        if (elementReports.Length > 0)
        {
            var element = Assert.IsType<Dictionary<string, object?>>(entry["element"]);
            Assert.Equal(elementReports, element["reports"]);
        }
    }
}
