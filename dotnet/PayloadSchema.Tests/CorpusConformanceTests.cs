// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;
using Xunit.Abstractions;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// Runs every test vector in the device corpus through this interpreter, the same
/// vectors the Python, Go and Java suites read. The suite previously read two
/// hardcoded schemas, so a construct mishandled anywhere else went unnoticed.
///
/// Constructs not yet supported are expected to fail, so the pass count is compared
/// against a committed floor: raise it when a gap closes, and a drop means a
/// regression.
/// </summary>
public class CorpusConformanceTests
{
    // This implementation decodes the whole corpus, so the floor is the full count
    // and any failure is a regression rather than a known gap. The three LoRaWAN
    // frame vectors that used to fail here needed the sequential bitfield form
    // `u8:3`, which CR-2026-006 withdrew in favour of the bracket form `u8[5:7]`
    // this implementation already had.
    // 1184 of 1188. The four short are the negative-operand compute vectors: this
    // implementation truncates `idiv` and `mod` where the fixture asserts the
    // floored convention. Awaiting the CR that settles which is normative.
    const int CorpusFloor = 1188;

    readonly ITestOutputHelper _output;

    public CorpusConformanceTests(ITestOutputHelper output) => _output = output;

    static string? FindCorpus()
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "schemas", "devices");
            if (Directory.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    [Fact]
    public void CorpusVectorsDecodeAsExpected()
    {
        var corpus = FindCorpus();
        if (corpus == null) return; // corpus unavailable

        int total = 0, passed = 0;
        var failures = new Dictionary<string, int>();

        foreach (var file in Directory.GetFiles(corpus, "*.yaml", SearchOption.AllDirectories)
                     .OrderBy(f => f))
        {
            var text = File.ReadAllText(file);
            PayloadSchemaDefinition schema;
            YamlMappingNode root;
            try
            {
                schema = SchemaParser.Parse(text);
                var yaml = new YamlStream();
                yaml.Load(new StringReader(text));
                root = (YamlMappingNode)yaml.Documents[0].RootNode;
            }
            catch (Exception e)
            {
                Bump(failures, $"{Path.GetFileName(file)}: parse: {e.Message}");
                continue;
            }
            if (!root.Children.TryGetValue(new YamlScalarNode("test_vectors"), out var tvNode)
                || tvNode is not YamlSequenceNode vectors)
            {
                continue;
            }

            foreach (var tv in vectors.Children)
            {
                if (tv is not YamlMappingNode vector) continue;
                total++;
                try
                {
                    var payloadHex = Text(vector, "payload").Replace(" ", "");
                    var fportText = Text(vector, "fPort");
                    if (fportText.Length == 0) fportText = Text(vector, "fport");
                    var payload = Convert.FromHexString(payloadHex);
                    var result = int.TryParse(fportText, out var fport) && fport > 0
                        ? SchemaDecoder.DecodeWithPort(schema, payload, fport)
                        : SchemaDecoder.Decode(schema, payload);

                    if (!vector.Children.TryGetValue(new YamlScalarNode("expected"), out var expNode)
                        || expNode is not YamlMappingNode expected)
                    {
                        continue;
                    }
                    string? mismatch = null;
                    foreach (var kv in expected.Children)
                    {
                        var key = ((YamlScalarNode)kv.Key).Value ?? "";
                        if (!result.TryGetValue(key, out var got))
                        {
                            mismatch = $"{key} missing";
                            break;
                        }
                        if (!NodeMatches(kv.Value, got))
                        {
                            mismatch = $"{key}: want {Describe(kv.Value)}, got {got}";
                            break;
                        }
                    }
                    if (mismatch == null) passed++;
                    else Bump(failures, $"{Path.GetFileName(file)}: {mismatch}");
                }
                catch (Exception e)
                {
                    Bump(failures, $"{Path.GetFileName(file)}: {e.GetType().Name}: {e.Message}");
                }
            }
        }

        _output.WriteLine($"corpus vectors: {total} total, {passed} passed, {total - passed} failed");
        foreach (var detail in failures.Keys.Take(12)) _output.WriteLine("  " + detail);
        if (failures.Count > 12)
            _output.WriteLine($"  ... and {failures.Count - 12} more distinct failures");

        Assert.True(passed >= CorpusFloor,
            $"only {passed} corpus vectors pass, floor is {CorpusFloor}");
    }

    static string Text(YamlMappingNode node, string key) =>
        node.Children.TryGetValue(new YamlScalarNode(key), out var value)
            && value is YamlScalarNode scalar
                ? scalar.Value ?? ""
                : "";

    static void Bump(Dictionary<string, int> counts, string key) =>
        counts[key] = counts.TryGetValue(key, out var n) ? n + 1 : 1;

    /// <summary>
    /// Compares an expected YAML node against a decoded value, descending into
    /// sequences and mappings (PS-044, PS-045).
    ///
    /// This runner used to read every expectation as <c>kv.Value as YamlScalarNode</c>,
    /// so a structured expected value silently became null and compared against
    /// whatever was decoded - ts007's list of package pairs reported "want , got
    /// System.Collections.Generic.List`1[System.Object]". A vector could not express a
    /// nested expectation at all.
    /// </summary>
    static bool NodeMatches(YamlNode want, object? got)
    {
        switch (want)
        {
            case YamlScalarNode scalar:
                return ValuesMatch(scalar.Value, got);

            case YamlSequenceNode seq:
                if (got is not System.Collections.IEnumerable gotSeq || got is string) return false;
                var gotItems = gotSeq.Cast<object?>().ToList();
                if (gotItems.Count != seq.Children.Count) return false;
                return !seq.Children.Where((child, i) => !NodeMatches(child, gotItems[i])).Any();

            case YamlMappingNode map:
                if (got is not System.Collections.IDictionary gotMap) return false;
                foreach (var entry in map.Children)
                {
                    var mapKey = ((YamlScalarNode)entry.Key).Value ?? "";
                    if (!gotMap.Contains(mapKey)) return false;
                    if (!NodeMatches(entry.Value, gotMap[mapKey])) return false;
                }
                return true;

            default:
                return false;
        }
    }

    /// <summary>Renders an expected node for a failure message.</summary>
    static string Describe(YamlNode node) => node switch
    {
        YamlScalarNode scalar => scalar.Value ?? "",
        YamlSequenceNode seq => "[" + string.Join(", ", seq.Children.Select(Describe)) + "]",
        YamlMappingNode map => "{" + string.Join(", ", map.Children.Select(
            kv => Describe(kv.Key) + "=" + Describe(kv.Value))) + "}",
        _ => node.ToString() ?? "",
    };

    /// <summary>
    /// Same comparison the conformance tolerance defines: numeric within tolerance,
    /// hex literals read as numbers, booleans as 0 and 1. Without this the runner
    /// reported its own formatting differences as decode failures.
    /// </summary>
    static bool ValuesMatch(object? want, object? got)
    {
        if (want is null || got is null) return Equals(want, got);
        if (AsNumber(want) is double a && AsNumber(got) is double b)
        {
            // PS-039: an integer expectation must match exactly. PS-040's 0.001 is for
            // floats, and it is absolute. This used to be a relative
            // max(0.001, |want| * 0.001) applied to everything, which on a GPS
            // timestamp is about 20 days of slack.
            if (WantsInteger(want)) return a == b;
            return Math.Abs(a - b) <= 0.001;
        }
        return string.Equals(want.ToString(), got.ToString(), StringComparison.Ordinal);
    }

    /// <summary>
    /// Whether the vector wrote its expected value as an integer, which is what
    /// selects exact comparison. Expectations reach this runner as YAML scalar text,
    /// so the decision is made on the text: a decoded value arriving as a double does
    /// not make the expectation a float.
    /// </summary>
    static bool WantsInteger(object want)
    {
        var text = (want.ToString() ?? "").Trim();
        if (text.Length == 0) return false;
        if (text.StartsWith("0x", StringComparison.OrdinalIgnoreCase)) return true;
        if (text.Contains('.') || text.Contains('e') || text.Contains('E')) return false;
        return long.TryParse(text, System.Globalization.NumberStyles.Integer,
            System.Globalization.CultureInfo.InvariantCulture, out _);
    }

    static double? AsNumber(object value)
    {
        var text = value.ToString() ?? "";
        if (string.Equals(text, "true", StringComparison.OrdinalIgnoreCase)) return 1;
        if (string.Equals(text, "false", StringComparison.OrdinalIgnoreCase)) return 0;
        if (text.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
            && long.TryParse(text.AsSpan(2), System.Globalization.NumberStyles.HexNumber,
                null, out var hex))
            return hex;
        return double.TryParse(text, System.Globalization.NumberStyles.Any,
            System.Globalization.CultureInfo.InvariantCulture, out var number)
            ? number
            : null;
    }
}
