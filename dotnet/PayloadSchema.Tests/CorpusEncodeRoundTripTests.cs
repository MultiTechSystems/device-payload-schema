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
    const int EncodeFloorTotal = 2203;   // +14: bitfield-string-padded-signed (CR-2026-170), plain fixed; +22: minifloat-reference-binding (11 types x 2; +13: match-fractional-range-keys (match; every vector round-trips); +4: bitfield-string-field-endian (two fixtures), plain fixed; +4: expected-error (CR-2026-167); +1: repeat-sampled (CR-2026-156); +1: negative-zero (CR-2026-154), plain fixed; +3: match-discriminator-from-inner-case (CR-2026-151: a nested match fixes the discriminator), all match; +11: no silent empty encode - length-selects-layout (4) and literal-selects-branch (2), match; +4: wide-peek-selector, wide-peek-selector-u64; +4: ascii-numeral (CR-2026-150); +6: bf16 (CR-2026-149; bf16-specials has no round trip); +12: the four tlv encode-gap fixtures (tlv-object-with-inner-branch, tlv-case-of-one-inline-branch, tlv-float-reading, tlv-scaled-wide-reading), all tlv, each vector failing before in all five (CR-2026-148 and the inline-branch claims); +2: tlv-name-object-and-scalar (a mapping is no value for a scalar case); +5: match-fractional-discriminator (match); -4 deliberately: PS-169 (CR-2026-146) rejects a label several codes share - vobo mutant_p32_01, mutant_p112_01, two_analog_xp_all, two_analog_xx_ain3 round-tripped only because the first code found was the right one; +4: mask-reference-binding (flagged); +16: CR-2026-141 (PS-551) derives a covered peek - the 26 milesight ct10x/ct30x/gs301/ws515 tlv vectors the line below lost come back - and rejects a supplied peek the bits after it contradict: vobo mutant_p32_03..07 and mutant_p112_03..07 (a lookup giving two codes one label), which round-tripped only because ORing happened to reproduce the payload; -26 deliberately: merging CR-2026-136 with CR-2026-134 - 26 tlv vectors of milesight ct10x/ct30x/gs301/ws515 round-tripped under CR-2026-136 by leaving a status peek's bits zero; the peek's lookup misses on an ordinary reading, so the decode omits it (PS-269), and PS-551 rejects the input that lacks it; +2: CR-2026-110 tlv-repeat-merge, and vobo modbus_generic_one_register_blocks (tied candidates now keep declaration order, as the reference does); +3: CR-2026-125 (tlv-repeat-merge number_keeps_collecting, tlv-repeat-collect-first-case, tlv-repeat-merge-shared-member); +2: CR-2026-128 (tlv-repeat-merge-shared-last, and tlv-repeat-object-collect now that each element finds its case); +3: CR-2026-134 (PS-551) derives internal-discriminator.yaml's `_kind` (both vectors, match) and flagged-group-field-list.yaml inner_groups' masks (flagged); +3: byte-group-width (CR-2026-131); +4: ascii-latin1 (CR-2026-114); +4: lookup-reference-binding vectors added for the mutation gate; +6: CR-2026-109/132 lookup-reference-binding (its miss, plain-default, enum-default and unnamed-enum vectors and the tag-component vectors do not round-trip); +1: merging CR-2026-109 with CR-2026-134 - lookup-reference-binding an_unnamed_enum_code_is_omitted_but_bound reports no discriminator, now derived from its case (PS-551); +265: +1 encoder-rounding round_trip (CR-2026-135, plain fixed); +264 CR-2026-136 bit ranges and bools laid out as the decoder reads them, a flagged block's given groups as one field list, and a construct's unconsumed peek carried to the field after it - all tlv-bucketed: vobo 150, dnt 75, milesight 39;  +6: CR-2026-121/123 fixtures (all five vectors) and repeat-element-null reserved_value, whose null now encodes as the sentinel (PS-544); +2: CR-2026-124 guard-set empty in/not_in (plain fixed); +4: match-u32-inline-discriminator (the peek fixture's internal word has no value to write back); +4: names-mangling-alike; +2: CR-2026-127 guard-boolean-operand; +2: CR-2026-118 guard-set (plain fixed; the records and present_if fixtures are refused, PS-542, PS-387); +6: registry divergence fixtures, all plain fixed (tag-component-ref does not round-trip); +5: CR-2026-111 fixtures (repeat-element-null's two null vectors are refused, PS-529); +8: name-from-rendering (CR-2026-112); +2: boolean-literal (CR-2026-107); +13: the nine TS013-defect fixtures (tlv-tag-bit-range does not round-trip); +8: CR-2026-104 lookup label fixtures (plain fixed); +3: byte-group-bool; +3: tlv-case-field-list; +2: tlv-case-key-patterns; +2: CR-2026-096 meta fixtures (meta-port-default, plain fixed); +6 before: trimmed vendor vectors

    /// <summary>
    /// Per-shape floors, so a regression in a layout that works cannot hide behind the mass
    /// of one that does not.
    /// </summary>
    static readonly Dictionary<string, int> EncodeFloorByShape = new()
    {
        // 1283 -> 1281 deliberately (CR-2026-067, PS-434): two vobo vectors round-tripped only because their internal byte halves were all zero, which the encoder wrote for an internal field with no value. It now reports such a field instead.
        ["tlv"] = 1579,   // +4: expected-error (CR-2026-167); +5: tag-entry-constants (4) and tlv-case-field-list literal_only, a constant now claims its entry (no silent empty); +12: the tlv encode-gap fixtures (see the total); +2: tlv-name-object-and-scalar (a mapping is no value for a scalar case); -4 deliberately: PS-169 (CR-2026-146), vobo mutant_p32_01, mutant_p112_01, two_analog_xp_all, two_analog_xx_ain3 (see the total); +16: CR-2026-141 (see the total): +26 milesight, -10 vobo; -26: CR-2026-136 with CR-2026-134 (see the total); +2: CR-2026-110 tlv-repeat-merge, and vobo modbus_generic_one_register_blocks (declaration-order tie-break); +3: CR-2026-125 (tlv-repeat-merge number_keeps_collecting, tlv-repeat-collect-first-case, tlv-repeat-merge-shared-member); +2: CR-2026-128 (tlv-repeat-merge-shared-last, and tlv-repeat-object-collect now that each element finds its case); +264: CR-2026-136, a peeked bit range or bool shares its byte with the field after it, inside a construct too (vobo 150, dnt 75, milesight 39); +3: tlv-case-field-list; +2: tlv-case-key-patterns; +6: trimmed vendor vectors (am30x, ws50x, em310-tilt) now round-trip
        ["flagged"] = 198,   // +4: mask-reference-binding; +1: CR-2026-134 derives flagged-group-field-list inner_groups' `_mask` and `_inner`, and encodes its nested flagged group
        ["plain fixed"] = 199,   // +14: bitfield-string-padded-signed (CR-2026-170), plain fixed; +22: minifloat-reference-binding (11 types x 2; plain fixed); +4: bitfield-string-field-endian (two fixtures); +1: negative-zero (CR-2026-154); +4: ascii-numeral (CR-2026-150); +6: bf16 (CR-2026-149); +4: ascii-latin1 (CR-2026-114); +1: encoder-rounding round_trip (CR-2026-135); +2: CR-2026-124 guard-set; +4: names-mangling-alike; +2: CR-2026-127 guard-boolean-operand; +2: CR-2026-118 guard-set; +6: registry divergence fixtures; +8: name-from-rendering (CR-2026-112); +8: CR-2026-104 lookup label fixtures; +9: TS13 fixtures; +2: CR-2026-96 meta-port-default.yaml
        ["match"] = 173,   // +13: match-fractional-range-keys (match; every vector round-trips); +4: wide-peek-selector, wide-peek-selector-u64; +3: match-discriminator-from-inner-case (CR-2026-151); +6: length-selects-layout and literal-selects-branch (no silent empty: a case scored through its nested constructs, a constant ruling a body out); +5: match-fractional-discriminator; +1: CR-2026-109 with CR-2026-134 (see the total); +5: lookup-reference-binding (CR-2026-109); +1: CR-2026-132 enum port; +2: CR-2026-134 derives internal-discriminator.yaml's `_kind` from the case given (PS-551); +4: match-u32-inline-discriminator
        ["byte_group"] = 28,   // +3: byte-group-width (CR-2026-131); +3: byte-group-bool; +2: wide-group
        // 6 -> 5 with plain fixed 66 -> 67: a bucket move. Composed library schemas now
        // carry only the definitions they reach, so one no longer contains an
        // unreferenced definition's `repeat` text for the classifier to find.
        ["repeat"] = 26,   // +1: repeat-sampled (CR-2026-156); +6: CR-2026-121/123 fixtures and repeat-element-null reserved_value (PS-544); +5: CR-2026-111 fixtures; +2: TS013 fixtures
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
