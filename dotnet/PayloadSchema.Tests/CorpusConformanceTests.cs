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
    // This implementation now decodes the whole corpus, so the floor is the full
    // count and any failure is a regression rather than a known gap.
    const int CorpusFloor = 1117;

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
                        var want = kv.Value is YamlScalarNode scalar ? scalar.Value : null;
                        if (!result.TryGetValue(key, out var got))
                        {
                            mismatch = $"{key} missing";
                            break;
                        }
                        if (!ValuesMatch(want, got))
                        {
                            mismatch = $"{key}: want {want}, got {got}";
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
    /// Same comparison the conformance tolerance defines: numeric within tolerance,
    /// hex literals read as numbers, booleans as 0 and 1. Without this the runner
    /// reported its own formatting differences as decode failures.
    /// </summary>
    static bool ValuesMatch(object? want, object? got)
    {
        if (want is null || got is null) return Equals(want, got);
        if (AsNumber(want) is double a && AsNumber(got) is double b)
            return Math.Abs(a - b) <= Math.Max(0.001, Math.Abs(a) * 0.001);
        return string.Equals(want.ToString(), got.ToString(), StringComparison.Ordinal);
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
