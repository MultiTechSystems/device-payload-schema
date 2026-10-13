using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-162 (PS-572 to PS-574): the decode record. <c>Interpret(..., decodeRecord: true)</c>
/// adds `_decode`; the encoder given it follows it and rejects input contradicting it; where
/// neither the input nor the record gives a match's case, this implementation derives it.
/// The cases are shared with the reference, the generated codec, Go and Java in
/// tests/cr-2026-162-cases.yaml.
/// </summary>
public class CR2026_162Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-162-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    static List<YamlMappingNode> Load()
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-162-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        return ((YamlSequenceNode)root.Children[new YamlScalarNode("cases")]).Children
            .Cast<YamlMappingNode>().ToList();
    }

    public static IEnumerable<object[]> Cases()
        => Load().Select(m => new object[] { Text(m, "name")! });

    static YamlNode? Child(YamlMappingNode map, string key)
        => map.Children.TryGetValue(new YamlScalarNode(key), out var v) ? v : null;

    static string? Text(YamlMappingNode map, string key)
        => Child(map, key) is YamlScalarNode s ? s.Value : null;

    static object? FromJson(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Object => e.EnumerateObject()
            .ToDictionary(p => p.Name, p => FromJson(p.Value)),
        JsonValueKind.Array => e.EnumerateArray().Select(FromJson).ToList(),
        JsonValueKind.String => e.GetString(),
        JsonValueKind.Number => e.TryGetInt64(out var l) ? (object)l : e.GetDouble(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        _ => null,
    };

    static object? ParseJson(string text)
    {
        using var doc = JsonDocument.Parse(text);
        return FromJson(doc.RootElement);
    }

    /// <summary>JSON values equal, numbers by value, a boolean only a boolean.</summary>
    static bool Same(object? want, object? got)
    {
        if (want is bool || got is bool) return Equals(want, got);
        if (want is long or double && got is long or double or int)
            return Convert.ToDouble(want) == Convert.ToDouble(got);
        if (want is Dictionary<string, object?> wm && got is IDictionary<string, object?> gm)
            return wm.Count == gm.Count && wm.All(kv => gm.TryGetValue(kv.Key, out var g) && Same(kv.Value, g));
        if (want is List<object?> wl && got is System.Collections.IList gl)
            return wl.Count == gl.Count && wl.Select((w, i) => Same(w, gl[i])).All(x => x);
        return Equals(want, got);
    }

    [Theory]
    [MemberData(nameof(Cases))]
    public void EveryCase(string name)
    {
        var c = Load().First(m => Text(m, "name") == name);
        var schema = SchemaParser.Parse(Text(c, "schema")!);
        int? fport = Text(c, "fport") is { } p ? int.Parse(p) : null;
        Dictionary<string, object?>? decoded = null;
        if (Child(c, "decode") is YamlMappingNode dec)
        {
            var payload = Convert.FromHexString(Text(dec, "payload")!);
            decoded = SchemaDecoder.Interpret(schema, payload, new InputContext(fport), decodeRecord: true);
            var record = (Dictionary<string, object?>)decoded["_decode"]!;
            if (Text(dec, "record") is { } want)
                Assert.True(Same(ParseJson(want), record),
                    $"[{name}] record {JsonSerializer.Serialize(record)}");
            if (Text(dec, "matches") is { } keys)
            {
                var expected = ((List<object?>)ParseJson(keys)!).Cast<string>().OrderBy(k => k);
                Assert.Equal(expected, ((Dictionary<string, object?>)record["matches"]!).Keys.OrderBy(k => k));
            }
            // decode() never carries it (PS-572).
            var plain = fport is { } port ? SchemaDecoder.DecodeWithPort(schema, payload, port)
                : SchemaDecoder.Decode(schema, payload);
            Assert.False(plain.ContainsKey("_decode"));
            if (Text(dec, "round_trip") is "true" or "True")
            {
                var result = fport is { } fp ? SchemaEncoder.EncodeWithPort(schema, decoded, fp)
                    : SchemaEncoder.Encode(schema, decoded);
                Assert.True(result.Success, $"[{name}] round trip: {string.Join(" | ", result.Errors)}");
                Assert.Equal(Text(dec, "payload")!.ToLowerInvariant(),
                    Convert.ToHexString(result.Payload).ToLowerInvariant());
            }
        }
        if (Child(c, "encode") is YamlSequenceNode steps)
        {
            foreach (var step in steps.Children.Cast<YamlMappingNode>())
            {
                var data = (Dictionary<string, object?>)ParseJson(Text(step, "input")!)!;
                Dictionary<string, object?>? record = Text(step, "record") switch
                {
                    null => null,
                    "decoded" => (Dictionary<string, object?>)decoded!["_decode"]!,
                    var json => (Dictionary<string, object?>)ParseJson(json)!,
                };
                var result = fport is { } fp ? SchemaEncoder.EncodeWithPort(schema, data, fp, decodeRecord: record)
                    : SchemaEncoder.Encode(schema, data, decodeRecord: record);
                if (Text(step, "error") is { } error)
                {
                    Assert.True(result.Errors.Any(e => e.Contains(error)),
                        $"[{name}] expected '{error}', got: {string.Join(" | ", result.Errors)}");
                    Assert.Empty(result.Payload);
                }
                else
                {
                    Assert.True(result.Success, $"[{name}] {Text(step, "input")}: {string.Join(" | ", result.Errors)}");
                    Assert.Equal(Text(step, "payload"), Convert.ToHexString(result.Payload).ToLowerInvariant());
                }
            }
        }
    }

    [Fact]
    public void A_field_named_decode_is_reserved()
    {
        // PS-433 as amended: `_decode` is reserved beside `_meta`, `_quality` and `_warnings`.
        var ex = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(
            "name: t\nfields:\n  - {name: _decode, type: u8}\n"));
        Assert.Contains("PS-433", ex.Message);
    }
}
