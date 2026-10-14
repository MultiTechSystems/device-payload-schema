using System.Text.Json;
using Xunit;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-175 (PS-453, PS-015, PS-588, PS-589): a bytes, hex or ascii field declaring
/// `consume: 0` reports its span and leaves the read position; an encoder writes nothing
/// for it and checks a value given for it against what the other fields write. The cases
/// are shared with the reference, the generated codec, Go and Java in
/// tests/cr-2026-175-cases.yaml.
/// </summary>
public class CR2026_175Tests
{
    static string? FindCases()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "tests", "cr-2026-175-cases.yaml");
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    public static IEnumerable<object[]> Section(string key)
    {
        var path = FindCases() ?? throw new FileNotFoundException("tests/cr-2026-175-cases.yaml");
        var stream = new YamlStream();
        using (var reader = new StreamReader(path))
            stream.Load(reader);
        var root = (YamlMappingNode)stream.Documents[0].RootNode;
        foreach (var node in ((YamlSequenceNode)root.Children[new YamlScalarNode(key)]).Children)
        {
            var map = (YamlMappingNode)node;
            string Get(string k) => map.Children.TryGetValue(new YamlScalarNode(k), out var v)
                ? ((YamlScalarNode)v).Value ?? "" : "";
            yield return new object[]
            {
                Get("name"), Get("schema"), Get("payload"), Get("input"), Get("expected"), Get("error"),
            };
        }
    }

    public static IEnumerable<object[]> DecodeCases() => Section("decode");
    public static IEnumerable<object[]> EncodeCases() => Section("cases");

    static object? FromJson(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Object => e.EnumerateObject()
            .ToDictionary(p => p.Name, p => FromJson(p.Value)),
        JsonValueKind.Array => e.EnumerateArray().Select(FromJson).ToList(),
        JsonValueKind.String => e.GetString(),
        JsonValueKind.Number => e.GetDouble(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        _ => null,
    };

    static bool Same(object? want, object? got)
    {
        switch (want)
        {
            case null: return got == null;
            case string s: return got is string g && g == s;
            case bool b: return got is bool gb && gb == b;
            case double d:
                return got is not (string or bool) && got != null
                    && Math.Abs(Convert.ToDouble(got, System.Globalization.CultureInfo.InvariantCulture) - d) < 1e-9;
            case Dictionary<string, object?> m:
                if (got is not IDictionary<string, object?> gm || gm.Count != m.Count) return false;
                return m.All(kv => gm.TryGetValue(kv.Key, out var v) && Same(kv.Value, v));
            case List<object?> l:
                if (got is not System.Collections.IList gl || gl.Count != l.Count) return false;
                return l.Select((v, i) => Same(v, gl[i])).All(x => x);
        }
        return false;
    }

    [Theory]
    [MemberData(nameof(DecodeCases))]
    public void Decode(string name, string schemaText, string payload, string input,
        string expected, string error)
    {
        var schema = SchemaParser.Parse(schemaText);
        var bytes = Convert.FromHexString(payload);
        if (error.Length > 0)
        {
            var e = Assert.ThrowsAny<Exception>(() => SchemaDecoder.Decode(schema, bytes));
            Assert.True(e.Message.Contains(error), $"[{name}] expected '{error}', got: {e.Message}");
        }
        else
        {
            var data = SchemaDecoder.Decode(schema, bytes);
            using var doc = JsonDocument.Parse(expected);
            var want = FromJson(doc.RootElement);
            Assert.True(Same(want, data),
                $"[{name}] expected {expected}, got {JsonSerializer.Serialize(data)}");
        }
    }

    [Theory]
    [MemberData(nameof(EncodeCases))]
    public void Encode(string name, string schemaText, string payload, string input,
        string expected, string error)
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
}
