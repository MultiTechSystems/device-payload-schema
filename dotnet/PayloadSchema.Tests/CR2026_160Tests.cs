using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-160 (0.5.6, PS-059): u40/u48/u56 and s40/s48/s56 are bit-range bases only. A standalone
/// <c>type: u56</c> is rejected at load; a bit range over one is read.
/// The cases are shared with the reference, Go and Java in tests/cr-2026-160-cases.yaml.
/// </summary>
public class CR2026_160Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-160-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-160-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode("cases")]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => map.Children.TryGetValue(new YamlScalarNode(key), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            yield return new object[] { Get("name"), Get("schema"), Get("payload"), Get("decoded"), Get("error") };
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
