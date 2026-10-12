using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// No silent empty encode (PS-442, PS-551): an encoder never returns success having
/// written nothing for a construct whose decode reads bytes - a match with no case
/// chosen, a tlv entry no case claimed - nor an empty payload where the input gives a
/// value only payload bytes carry. The cases are shared with the reference, the
/// generated codec, Go and Java in tests/silent-empty-cases.yaml.
/// </summary>
public class SilentEmptyTests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "silent-empty-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Cases()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/silent-empty-cases.yaml");
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
                Get("name"), Get("schema"), Get("input"),
                map.Children.ContainsKey(new YamlScalarNode("payload")) ? Get("payload") : "",
                map.Children.ContainsKey(new YamlScalarNode("error")) ? Get("error") : "",
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
    public void EveryCase(string name, string schemaText, string input, string payload, string error)
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

    static readonly List<SchemaField> GuardFields = SchemaParser.Parse(
        "name: t\nfields:\n"
        + "  - {name: battery, type: u8}\n"
        + "  - match:\n      remaining: true\n      cases:\n        2:\n"
        + "          - {name: _w, type: u16}\n"
        + "          - {name: distance, type: number, ref: $_w}\n"
        + "      default:\n"
        + "        - {name: distance, type: string, value: none}\n"
        + "        - {name: kind, type: number, value: 4}\n").Fields;

    // The last line, which no path reaches today: each construct now fails by name
    // first. Held directly, as the reference holds empty_payload_error.
    [Fact]
    public void DeclaredSourcesTellReadsFromConstants()
    {
        var sources = SchemaEncoder.DeclaredSources(GuardFields);
        Assert.True(sources["battery"].reads);
        Assert.Empty(sources["battery"].literals);
        Assert.True(sources["distance"].reads);
        Assert.Equal(new object?[] { "none" }, sources["distance"].literals);
        Assert.False(sources["kind"].reads);
        Assert.True(Helpers.SameLabel(4, sources["kind"].literals.Single()));
    }

    [Fact]
    public void AValueOnlyBytesCarryIsNamed()
    {
        Assert.Equal("Encoding wrote no bytes, though the input gives 'battery', which the "
            + "schema reads from the payload (PS-442)",
            SchemaEncoder.EmptyPayloadError(GuardFields, new() { ["battery"] = 3L }));
    }

    [Fact]
    public void AValueAConstantGivesIsNot()
    {
        Assert.Null(SchemaEncoder.EmptyPayloadError(GuardFields,
            new() { ["distance"] = "none", ["kind"] = 4L }));
    }

    [Fact]
    public void AnotherValueOfANameBytesAlsoCarryIsNamed()
    {
        Assert.Contains("'distance'", SchemaEncoder.EmptyPayloadError(GuardFields,
            new() { ["distance"] = 12L }));
    }

    [Fact]
    public void ValuesWrittenInNoBytesAreNot()
    {
        Assert.Null(SchemaEncoder.EmptyPayloadError(GuardFields, new()
        {
            ["battery"] = new List<object?>(), ["distance"] = "", ["_w"] = 5L,
            ["other"] = 1L, ["x"] = null,
        }));
    }
}
