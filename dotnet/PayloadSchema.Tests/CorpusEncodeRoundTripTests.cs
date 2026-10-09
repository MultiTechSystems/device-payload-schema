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
    // CR-2026-031's name_from var-mismatch fixture round-trips here too, so
    // `plain fixed` rises from 59 to 61 and the total to 1164.
    // CR-2026-071/073's six arithmetic-order vectors round-trip (a computed field writes
    // nothing, and the read fields' arithmetic was already undone in reverse), so
    // `plain fixed` rises from 88 to 94; with one `match` vector the floor had not
    // caught up with, 122 to 123, the total rises to 1686.
    // CR-2026-085/086: measured 1692 with the floor at 1688. range-before-lookup.yaml's
    // three in-range vectors and leftover-bytes.yaml's exact frame round-trip, all under
    // `plain fixed` (96 -> 100) - the range fixture's field was renamed from `flagged`,
    // which the raw-YAML classifier had filed under that shape. Its out_of_range vector
    // omits a field and cannot (bytes differ), and leftover-bytes' second vector
    // re-encodes to its first byte only (PS-474): both by design.
    const int EncodeFloorTotal = 1692;

    /// <summary>
    /// Per-shape floors, so a regression in a layout that works cannot hide behind the mass
    /// of one that does not.
    /// </summary>
    static readonly Dictionary<string, int> EncodeFloorByShape = new()
    {
        // 1283 -> 1281 deliberately (CR-2026-067, PS-434): two vobo vectors round-tripped only because their internal byte halves were all zero, which the encoder wrote for an internal field with no value. It now reports such a field instead.
        ["tlv"] = 1281,
        ["flagged"] = 157,
        ["plain fixed"] = 100,
        ["match"] = 123,
        ["byte_group"] = 19,
        // 6 -> 5 with plain fixed 66 -> 67: a bucket move. Composed library schemas now
        // carry only the definitions they reach, so one no longer contains an
        // unreferenced definition's `repeat` text for the classifier to find.
        ["repeat"] = 12,
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
        // ENCODE_REPORT=/path/file.txt writes one "schema<TAB>vector<TAB>status" line per
        // decoded vector, so the vectors that start or stop round-tripping can be named
        // rather than inferred from a count.
        var reportPath = Environment.GetEnvironmentVariable("ENCODE_REPORT");
        var reportLines = new List<string>();

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
                var vectorName = Text(vector, "name");
                var rel = Path.GetRelativePath(corpus, file).Replace('\\', '/');
                void Report(string status) => reportLines.Add($"{rel}\t{vectorName}\t{status}");

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
                    Report("error");
                    continue;
                }

                string status;
                if (!result.Success)
                {
                    Bump(counts, status = "error");
                    Bump(errorDetail, $"{Path.GetFileName(file)}: {result.Errors[0]}");
                }
                else if (result.Payload.AsSpan().SequenceEqual(payload))
                {
                    Bump(counts, status = "round-trips");
                }
                else if (result.Payload.Length != payload.Length)
                {
                    Bump(counts, status = "length differs");
                }
                else
                {
                    Bump(counts, status = "bytes differ");
                }
                Report(status);
            }
        }

        if (!string.IsNullOrEmpty(reportPath))
            File.WriteAllLines(reportPath, reportLines);

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
