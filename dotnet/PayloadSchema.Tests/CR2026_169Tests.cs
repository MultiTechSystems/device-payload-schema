using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-169 (0.5.7, PS-581): which port entry an encoder uses. A caller's FPort chooses
/// its entry (or `default`), an uplink entry is refused only for a downlink; without an
/// FPort the single declared downlink entry, else the single downlink-capable one, else a
/// rejection. The cases are shared with the reference, the generated codec, Go and Java in
/// tests/cr-2026-169-cases.yaml. A zero fPort is none given: ports are 1 to 255.
/// </summary>
public class CR2026_169Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-169-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-169-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode("cases")]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string key) => map.Children.TryGetValue(new YamlScalarNode(key), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            yield return new object[]
            {
                Get("name"), Get("schema"), Get("input"), Get("fport"), Get("direction"),
                Get("payload"), Get("used"), Get("error"),
            };
        }
    }

    static object? FromJson(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Object => e.EnumerateObject()
            .ToDictionary(p => p.Name, p => FromJson(p.Value)),
        JsonValueKind.Array => e.EnumerateArray().Select(FromJson).ToList(),
        JsonValueKind.String => e.GetString(),
        JsonValueKind.Number => e.TryGetInt64(out var l) ? l : e.GetDouble(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        _ => null,
    };

    [Theory]
    [MemberData(nameof(Cases))]
    public void EveryCase(string name, string schemaText, string input, string fport,
        string direction, string payload, string used, string error)
    {
        var schema = SchemaParser.Parse(schemaText);
        using var doc = JsonDocument.Parse(input);
        var data = (Dictionary<string, object?>)FromJson(doc.RootElement)!;
        var result = SchemaEncoder.EncodeWithPort(schema, data,
            fport.Length > 0 ? int.Parse(fport) : 0,
            direction: direction == "none" ? null : SchemaDirection.Downlink);
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
            Assert.Equal(int.Parse(used), result.FPort);
        }
    }

    /// <summary>PS-459 is a decoder's rule: an encoder given no FPort chooses an entry.</summary>
    [Fact]
    public void EncodeWithoutAPortChoosesTheDownlinkEntry()
    {
        var schema = SchemaParser.Parse(
            "name: t\nports:\n  2: {direction: downlink, fields: [{name: a, type: u8}]}\n");
        var result = SchemaEncoder.Encode(schema, new Dictionary<string, object?> { ["a"] = 3L });
        Assert.Equal("03", Convert.ToHexString(result.Payload).ToLowerInvariant());
        Assert.Equal(2, result.FPort);
    }
}
