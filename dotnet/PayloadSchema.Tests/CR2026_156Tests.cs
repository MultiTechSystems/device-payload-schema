using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-156 (0.5.5, PS-567 to PS-569): <c>sampled</c> on a repeat. A good declaration is
/// carried by <c>_meta</c> with its defaults filled in, in the reference's key order, and
/// changes no decoded value; a bad one is rejected at load with the spec prototype's text.
/// The cases are shared with the reference, Go and Java in tests/cr-2026-156-cases.yaml.
/// </summary>
public class CR2026_156Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-156-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-156-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode("cases")]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => map.Children.TryGetValue(new YamlScalarNode(key), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            yield return new object[] { Get("name"), Get("schema"), Get("payload"), Get("sampled"), Get("decoded"), Get("error") };
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
    public void EveryCase(string name, string schemaText, string payload, string sampled, string decoded, string error)
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
        var entry = Assert.IsType<Dictionary<string, object?>>(fields["pm25"]);
        using var want = JsonDocument.Parse(sampled);
        Assert.Equal(Norm(want.RootElement), Norm(entry["sampled"]));
        if (decoded.Length > 0)
        {
            using var wantDecoded = JsonDocument.Parse(decoded);
            Assert.Equal(Norm(wantDecoded.RootElement), Norm(output["pm25"]));
        }
    }
}
