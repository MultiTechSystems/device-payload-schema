using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-194 (PS-436, PS-437, PS-438, PS-440, PS-270): a one-field tlv tag's "[*]" and "[!n]"
/// keys rank below exact keys, whatever the declaration order, and a tlv has no <c>default</c>
/// case. The cases are shared with the reference, Go and Java in tests/cr-2026-194-cases.yaml.
/// </summary>
public class CR2026_194Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-194-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-194-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode("cases")]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => map.Children.TryGetValue(new YamlScalarNode(key), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            if (Get("error").Length > 0)
            {
                yield return new object[] { Get("name"), Get("schema"), "", "", Get("error") };
                continue;
            }
            // One row per payload the case decodes.
            var decodes = (YamlSequenceNode)map.Children[new YamlScalarNode("decodes")];
            foreach (var d in decodes.Children.Cast<YamlMappingNode>())
                yield return new object[] { Get("name") + ":" + ((YamlScalarNode)d.Children[new YamlScalarNode("payload")]).Value,
                    Get("schema"), ((YamlScalarNode)d.Children[new YamlScalarNode("payload")]).Value ?? "",
                    ((YamlScalarNode)d.Children[new YamlScalarNode("decoded")]).Value ?? "", "" };
        }
    }

    /// <summary>A canonical text: keys in order, every number as a double.</summary>
    static string Norm(object? v) => v switch
    {
        JsonElement e => e.ValueKind switch
        {
            JsonValueKind.Object => "{" + string.Concat(e.EnumerateObject().Select(p => p.Name + "=" + Norm(p.Value) + ",")) + "}",
            JsonValueKind.Array => "[" + string.Concat(e.EnumerateArray().Select(x => Norm(x) + ",")) + "]",
            JsonValueKind.Number => e.GetDouble().ToString("R", System.Globalization.CultureInfo.InvariantCulture),
            JsonValueKind.String => e.GetString() ?? "",
            _ => e.ToString(),
        },
        Dictionary<string, object?> m => "{" + string.Concat(m.Select(p => p.Key + "=" + Norm(p.Value) + ",")) + "}",
        System.Collections.IList l => "[" + string.Concat(l.Cast<object?>().Select(x => Norm(x) + ",")) + "]",
        long or int or double or float => Convert.ToDouble(v).ToString("R", System.Globalization.CultureInfo.InvariantCulture),
        _ => v?.ToString() ?? "",
    };

    [Theory]
    [MemberData(nameof(Cases))]
    public void EveryCase(string name, string schemaText, string payload, string decoded, string error)
    {
        if (error.Length > 0)
        {
            var ex = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(schemaText));
            Assert.True(ex.Message.Contains(error), $"[{name}] expected '{error}', got: {ex.Message}");
            return;
        }
        var schema = SchemaParser.Parse(schemaText);
        var output = SchemaDecoder.Decode(schema, Convert.FromHexString(payload));
        using var want = JsonDocument.Parse(decoded);
        Assert.Equal(Norm(want.RootElement), Norm(output));
    }
}
