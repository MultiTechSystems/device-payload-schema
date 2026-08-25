// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;
using Xunit.Abstractions;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// Encoding, measured against the decode corpus - the C# side of
/// tests/test_encode_round_trip.py, go/schema/corpus_encode_test.go and
/// CorpusEncodeRoundTripTest.java.
///
/// <para>Every corpus vector tested decoding and nothing tested encoding, because until now
/// this implementation had no encoder to test: Helpers.EncodeUint and its siblings existed,
/// nothing that used them. encode(decode(payload)) == payload is the assertion the corpus
/// gives away for free, and it is what found the gaps worth fixing in the reference
/// interpreter.</para>
///
/// <para>It cannot hold everywhere. A `skip` field's bytes are not recoverable from output
/// that omits them, a rounding stage discards precision, and a `lookup` `default` label
/// stands for every value the table does not list (PS-269). So the floors are ratchets, not
/// a target of 1191.</para>
/// </summary>
public class CorpusEncodeRoundTripTests
{
    /// <summary>
    /// Corpus vectors that re-encode to their exact payload. Raise it as encoding improves;
    /// never lower it without saying why.
    /// </summary>
    // CR-2026-024 packed a bare run of bit ranges the way byte_group was already
    // packed, so the three LoRaWAN header schemas round-trip: `plain fixed` rises from
    // 55 to 58 and the total to 1145.
    // CR-2026-027 gave this encoder the `default:` key beside a match's `cases`, so
    // match-default-fields.yaml round-trips: `match` rises from 43 to 44 and the
    // total to 1147.
    // CR-2026-028 gave this encoder the word-ordered u32le16/s32le16 case it never
    // had, so the flagged members of that type round-trip: `flagged` rises from 121
    // to 135 and the total to 1161.
    // CR-2026-030 resolved a `name_from` template on encode, so name-from.yaml
    // round-trips: `plain fixed` rises from 58 to 59 and the total to 1162.
    const int EncodeFloorTotal = 1162;

    /// <summary>
    /// Per-shape floors, so a regression in a layout that works cannot hide behind the mass
    /// of one that does not.
    /// </summary>
    static readonly Dictionary<string, int> EncodeFloorByShape = new()
    {
        ["tlv"] = 901,
        ["flagged"] = 135,
        ["plain fixed"] = 59,
        ["match"] = 44,
        ["byte_group"] = 17,
        ["repeat"] = 6,
    };

    readonly ITestOutputHelper _output;

    public CorpusEncodeRoundTripTests(ITestOutputHelper output) => _output = output;

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

    /// <summary>The construct that dominates a schema's layout.</summary>
    static string SchemaShape(string raw)
    {
        foreach (var key in new[] { "tlv:", "match:", "repeat", "flagged:", "byte_group:" })
            if (raw.Contains(key))
                return key.EndsWith(":") ? key[..^1] : key;
        return "plain fixed";
    }

    [Fact]
    public void CorpusVectorsReEncodeToTheirPayload()
    {
        var corpus = FindCorpus();
        if (corpus == null) return; // corpus unavailable

        var byShape = new SortedDictionary<string, Dictionary<string, int>>();
        var errorDetail = new Dictionary<string, int>();
        int decoded = 0;

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
            catch (Exception)
            {
                continue;
            }
            if (!root.Children.TryGetValue(new YamlScalarNode("test_vectors"), out var tvNode)
                || tvNode is not YamlSequenceNode vectors)
            {
                continue;
            }

            var shape = SchemaShape(text);
            if (!byShape.TryGetValue(shape, out var counts))
                byShape[shape] = counts = new Dictionary<string, int>();

            foreach (var tv in vectors.Children)
            {
                if (tv is not YamlMappingNode vector) continue;
                byte[] payload;
                int fport;
                try
                {
                    payload = Convert.FromHexString(Text(vector, "payload").Replace(" ", ""));
                    var fportText = Text(vector, "fPort");
                    if (fportText.Length == 0) fportText = Text(vector, "fport");
                    int.TryParse(fportText, out fport);
                }
                catch (Exception)
                {
                    continue;
                }

                Dictionary<string, object?> data;
                try
                {
                    data = fport > 0
                        ? SchemaDecoder.DecodeWithPort(schema, payload, fport)
                        : SchemaDecoder.Decode(schema, payload);
                }
                catch (Exception)
                {
                    continue;   // a decode gap is CorpusConformanceTests' business
                }
                decoded++;

                EncodeResult result;
                try
                {
                    result = fport > 0
                        ? SchemaEncoder.EncodeWithPort(schema, data, fport)
                        : SchemaEncoder.Encode(schema, data);
                }
                catch (Exception e)
                {
                    Bump(counts, "error");
                    Bump(errorDetail, $"{Path.GetFileName(file)}: {e.GetType().Name}: {e.Message}");
                    continue;
                }

                if (!result.Success)
                {
                    Bump(counts, "error");
                    Bump(errorDetail, $"{Path.GetFileName(file)}: {result.Errors[0]}");
                }
                else if (result.Payload.AsSpan().SequenceEqual(payload))
                {
                    Bump(counts, "round-trips");
                }
                else if (result.Payload.Length != payload.Length)
                {
                    Bump(counts, "length differs");
                }
                else
                {
                    Bump(counts, "bytes differ");
                }
            }
        }

        int exact = 0;
        foreach (var (shape, counts) in byShape)
        {
            exact += counts.GetValueOrDefault("round-trips");
            _output.WriteLine($"{shape,-12} round-trips={counts.GetValueOrDefault("round-trips"),-5} "
                + $"length={counts.GetValueOrDefault("length differs"),-4} "
                + $"bytes={counts.GetValueOrDefault("bytes differ"),-4} "
                + $"error={counts.GetValueOrDefault("error"),-4}");
        }
        _output.WriteLine($"total round-trips: {exact} of {decoded} vectors decoded");
        foreach (var (detail, count) in errorDetail.OrderByDescending(kv => kv.Value).Take(12))
            _output.WriteLine($"  {count}x {detail}");

        var problems = new List<string>();
        if (exact < EncodeFloorTotal)
            problems.Add($"only {exact} corpus vectors re-encode exactly, "
                + $"floor is {EncodeFloorTotal}");
        foreach (var (shape, floor) in EncodeFloorByShape)
        {
            int got = byShape.GetValueOrDefault(shape)?.GetValueOrDefault("round-trips") ?? 0;
            if (got < floor)
                problems.Add($"{shape}: {got} re-encode exactly, floor is {floor}");
        }
        Assert.True(problems.Count == 0, string.Join("; ", problems));
    }

    static string Text(YamlMappingNode node, string key) =>
        node.Children.TryGetValue(new YamlScalarNode(key), out var value)
            && value is YamlScalarNode scalar
                ? scalar.Value ?? ""
                : "";

    static void Bump(Dictionary<string, int> counts, string key) =>
        counts[key] = counts.GetValueOrDefault(key) + 1;
}
