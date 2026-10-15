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
    // CR-2026-007 settled the floored `idiv`/`mod` convention and this
    // implementation follows it, so the negative-operand vectors pass and the floor
    // is the full corpus.
    // CR-2026-014's `expected_warnings` added three fixtures for the `unknown`
    // parameter, which no device schema sets, and the floor had drifted 29 below the
    // full count as vectors were added without it being raised. It is the full count
    // again: 1222.
    // CR-2026-020 brought the five implementations onto the same `match`, CR-2026-021
    // the same repeat `max`, and CR-2026-022 the same byte_length span, so their
    // fixtures pass everywhere and the full count is 1237.
    // CR-2026-031 added the name_from var-mismatch fixture, whose two vectors decode
    // everywhere, so the full count is 1239.
    // CR-2026-071/073 brought a computed field's arithmetic onto PS-443/PS-444, so the
    // three arithmetic-order fixtures decode here: the full count of payload vectors
    // is 2363 (2374 vectors, 11 without a payload or an expected block).
    // CR-2026-085/086 added leftover-bytes.yaml (2 vectors) and range-before-lookup.yaml
    // (4), and match-default-skip.yaml's skipped body now expects the PS-472 warning.
    // All six new vectors decode here, so the floor is the full 2371 (2382 vectors, 11
    // without a payload or an expected block).
    const int CorpusFloor = 2847;   // +5: bytes-encode-exact (CR-2026-181); +14: bitfield-string-padded-signed (CR-2026-170); +7: compute-pow (CR-2026-172); +9: byte-string-reread (CR-2026-175); +10: bytes-format-forms (all plain fixed; every vector round-trips); +11: byte-group-member-var; +1: round-ties-up (CR-2026-171); +22: minifloat-reference-binding (11 types x 2); +13: match-fractional-range-keys (match; every vector round-trips); +4: bitfield-string-field-endian (two fixtures); +7: expected-error (CR-2026-167: three decode vectors expecting a failure, four that decode); +1: repeat-sampled (CR-2026-156); +3: negative-zero (2) and bf16 negative_zero (CR-2026-154); +9: bf16 and bf16's infinity and NaN vectors (CR-2026-149); +3: match-discriminator-from-inner-case (CR-2026-151); +10: no-silent-empty fixtures length-selects-layout, literal-selects-branch, tag-entry-constants; +5: wide-peek-selector, wide-peek-selector-u64; +4: ascii-numeral (CR-2026-150); +9: bf16 and bf16-specials (CR-2026-149); +12: the four tlv encode-gap fixtures (tlv-object-with-inner-branch, tlv-case-of-one-inline-branch, tlv-float-reading, tlv-scaled-wide-reading); +2: tlv-name-object-and-scalar; +5: match-fractional-discriminator (a fraction matches no single key); +4: mask-reference-binding; +16: CR-2026-109/132 lookup-reference-binding (14 vectors) and tag-component-reference-binding (2); +4: ascii-latin1 (CR-2026-114); +1: CR-2026-128 tlv-repeat-merge-shared-last; +2: CR-2026-125 tlv-repeat-collect-first-case, tlv-repeat-merge-shared-member; +6: CR-2026-110 tlv-repeat-* fixtures; +3: byte-group-width (CR-2026-131); +2: CR-2026-135/136 fixtures encoder-rounding and arithmetic-worked-examples (decode vectors); +1: round-large-value just_above_one (mutation gate); +5: CR-2026-121/123 fixtures (repeat-element-null-sentinel, repeat-element-internal-name, repeat-identity-sibling x2, repeat-count-computed); +2: CR-2026-124 guard-set empty in/not_in; +8: match-u32-peek-discriminator and match-u32-inline-discriminator; +4: names-mangling-alike; +2: CR-2026-127 guard-boolean-operand; +2: CR-2026-117 guard-absent-operand; +10: CR-2026-118 element-several-readings, element-case-several-readings, present-if-set, guard-set; +8: registry divergence fixtures (tag-component-ref, reference-to-absent, zero-length, numeric-looking-text, name-from-object, round-large-value); +7: CR-2026-111 repeat-element-* and repeat-index-base; +2: flagged-group-field-list; +8: name-from-rendering (CR-2026-112); +2: boolean-literal (CR-2026-107); +14: the nine TS013-defect fixtures (bitfield-string-wide, wide-bit-range, ...); +8: CR-2026-104 lookup-boolean-labels and lookup-number-labels; +3: byte-group-bool; +11: tlv-case-field-list, object-member-field-list, tlv-tag-var-name-from; +5: tlv-case-key-patterns; +1: CR-2026-097 meta-tlv-channels; +9: CR-2026-096 meta fixtures; dl-atm41g2's two vendor vectors (+2); +121: vendor-codec vectors that kill vobo's mutation survivors

    readonly ITestOutputHelper _output;

    public CorpusConformanceTests(ITestOutputHelper output) => _output = output;

    /// <summary>
    /// Why an error does not satisfy a vector's <c>expected_error</c>, or null where it does
    /// (PS-576). The PS numbers the message cites must include every requirement listed,
    /// checked only where it cites any (an implementation need not say which rule it
    /// enforced), and <c>at</c> must be named in it, quoted, or be a JSON Pointer it
    /// contains. Wording is never compared.
    /// </summary>
    static string? ErrorMismatch(string message, YamlMappingNode expectedError)
    {
        var wanted = new List<string>();
        if (expectedError.Children.TryGetValue(new YamlScalarNode("requirement"), out var req))
        {
            if (req is YamlSequenceNode list)
                wanted.AddRange(list.Children.OfType<YamlScalarNode>().Select(n => n.Value ?? ""));
            else if (req is YamlScalarNode one)
                wanted.Add(one.Value ?? "");
        }
        var cited = System.Text.RegularExpressions.Regex.Matches(message, @"PS-\d+[a-z]?")
            .Select(m => m.Value).ToHashSet();
        if (cited.Count > 0 && !wanted.All(cited.Contains))
            return $"the error cites {string.Join(",", cited.OrderBy(c => c, StringComparer.Ordinal))}, not {string.Join(",", wanted)}: {message}";
        if (expectedError.Children.TryGetValue(new YamlScalarNode("at"), out var atNode)
            && atNode is YamlScalarNode at && at.Value != null)
        {
            if (!message.Contains("'" + at.Value + "'")
                && !(at.Value.StartsWith('/') && message.Contains(at.Value)))
                return $"the error does not name \"{at.Value}\": {message}";
        }
        return null;
    }

    /// <summary>
    /// How the warnings a decode produced differ from what the vector expects, or null
    /// where they agree or the vector asserts nothing (PS-305 to PS-308).
    ///
    /// Absent and <c>[]</c> mean different things: absent asserts nothing, which is most
    /// of the corpus, while <c>[]</c> asserts that no warning was reported - the form
    /// that catches a schema edit beginning to discard data. Entries are matched as
    /// substrings, because the specification fixes what a warning must contain and not
    /// its wording (PS-306), and positionally against a complete list (PS-305), so an
    /// unexpected warning fails just as a missing one does.
    /// </summary>
    static string? WarningsMismatch(YamlMappingNode vector, Dictionary<string, object?> result)
    {
        if (!vector.Children.TryGetValue(new YamlScalarNode("expected_warnings"), out var node)
            || node is not YamlSequenceNode declared)
        {
            return null;
        }
        // An entry is a string, or a list of strings all of which must appear in that one
        // warning (PS-306): the tag and the byte count are not contiguous in any
        // implementation's text.
        var want = declared.Children
            .Select(entry => entry switch
            {
                YamlScalarNode scalar => new List<string> { scalar.Value ?? "" },
                YamlSequenceNode parts => parts.Children.OfType<YamlScalarNode>()
                    .Select(part => part.Value ?? "").ToList(),
                _ => new List<string>(),
            })
            .ToList();
        var got = result.TryGetValue("_warnings", out var reported) && reported is List<string> list
            ? list
            : new List<string>();
        if (got.Count != want.Count)
        {
            return $"expected {want.Count} warning(s), got {got.Count}: "
                   + string.Join(" | ", got);
        }
        for (int i = 0; i < want.Count; i++)
        {
            foreach (var fragment in want[i])
            {
                if (!got[i].Contains(fragment))
                    return $"warning[{i}]: \"{fragment}\" not found in \"{got[i]}\"";
            }
        }
        return null;
    }

    static string? FindCorpus()
    {
        var rootOverride = Environment.GetEnvironmentVariable("CORPUS_ROOT"); // see CorpusReport
        if (!string.IsNullOrEmpty(rootOverride)) return rootOverride;
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
        var report = new CorpusReport();
        var metaReport = new CorpusMetaReport();

        foreach (var file in Directory.GetFiles(corpus, "*.yaml", SearchOption.AllDirectories)
                     .OrderBy(f => f))
        {
            var rel = CorpusReport.Relative(Path.GetRelativePath(corpus, file));
            if (!report.Includes(rel)) continue;
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
                report.SchemaFailed(rel, text, e.Message);
                MetaSchemaFailed(metaReport, rel, text);
                continue;
            }
            if (!root.Children.TryGetValue(new YamlScalarNode("test_vectors"), out var tvNode)
                || tvNode is not YamlSequenceNode vectors)
            {
                continue;
            }

            for (int index = 0; index < vectors.Children.Count; index++)
            {
                if (vectors.Children[index] is not YamlMappingNode vector) continue;
                total++;
                var vectorName = CorpusReport.Scalar(vector, "name");
                // CR-2026-096: the interpreter output's _meta, for every vector with a
                // payload when CORPUS_META_REPORT asks, and wherever a vector pins it.
                var expectsMeta = vector.Children.TryGetValue(new YamlScalarNode("expected_meta"), out var expectedMeta);
                object? meta = null;
                object? decodeRecord = null;
                string? metaError = null;
                if ((metaReport.Enabled || expectsMeta)
                    && vector.Children.ContainsKey(new YamlScalarNode("payload")))
                {
                    try
                    {
                        // CR-2026-162: the report carries the decode record too, for rule 4.
                        var interpreted = SchemaDecoder.Interpret(schema,
                            Convert.FromHexString(Text(vector, "payload").Replace(" ", "")), MetaContext(vector),
                            decodeRecord: metaReport.Enabled);
                        meta = interpreted["_meta"];
                        decodeRecord = interpreted.GetValueOrDefault("_decode");
                    }
                    catch (Exception e)
                    {
                        metaError = $"{e.GetType().Name}: {e.Message}";
                    }
                    metaReport.Add(rel, index, vectorName, meta, decodeRecord);
                }
                try
                {
                    // An encode vector carries the values to encode and no payload to decode
            // (PS-047); it is not a failed decode. tools/vector-verdicts.py runs those
            // on both conformance paths.
            var payloadHex = Text(vector, "payload").Replace(" ", "");
            if (payloadHex.Length == 0)
            {
                report.Add(rel, index, vectorName, "skip", "no payload (encode vector)");
                continue;
            }
                    var fportText = Text(vector, "fPort");
                    if (fportText.Length == 0) fportText = Text(vector, "fport");
                    var payload = Convert.FromHexString(payloadHex);
                    if (vector.Children.TryGetValue(new YamlScalarNode("expected_error"), out var errNode)
                        && errNode is YamlMappingNode expectedError)
                    {
                        // CR-2026-167 (PS-576): the decode must fail, and its error must
                        // meet the vector. A decode that succeeds is the failure.
                        string? problem;
                        try
                        {
                            if (int.TryParse(fportText, out var errPort) && errPort > 0)
                                SchemaDecoder.DecodeWithPort(schema, payload, errPort);
                            else
                                SchemaDecoder.Decode(schema, payload);
                            problem = "succeeded where an error was expected";
                        }
                        catch (Exception e)
                        {
                            problem = ErrorMismatch(e.Message, expectedError);
                        }
                        if (problem == null)
                        {
                            passed++;
                            report.Add(rel, index, vectorName, "pass", "");
                        }
                        else
                        {
                            Bump(failures, $"{Path.GetFileName(file)}: {problem}");
                            report.Add(rel, index, vectorName, "fail", problem);
                        }
                        continue;
                    }
                    var result = int.TryParse(fportText, out var fport) && fport > 0
                        ? SchemaDecoder.DecodeWithPort(schema, payload, fport)
                        : SchemaDecoder.Decode(schema, payload);

                    if (!vector.Children.TryGetValue(new YamlScalarNode("expected"), out var expNode)
                        || expNode is not YamlMappingNode expected)
                    {
                        report.Add(rel, index, vectorName, "skip", "no expected block");
                        continue;
                    }
                    string? mismatch = WarningsMismatch(vector, result);
                    foreach (var kv in expected.Children)
                    {
                        if (mismatch != null) break;
                        var key = ((YamlScalarNode)kv.Key).Value ?? "";
                        if (IsNull(kv.Value))
                        {
                            // PS-043 (CR-2026-075): null asserts the key is absent.
                            if (result.TryGetValue(key, out var reported))
                            {
                                mismatch = $"{key}: reported {reported}, expected absent";
                                break;
                            }
                            continue;
                        }
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
                    if (mismatch == null && expectsMeta)
                    {
                        mismatch = metaError != null
                            ? "_meta: interpret failed: " + metaError
                            : MetaMismatch(Meta.FromYaml(expectedMeta), meta, "_meta");
                    }
                    if (mismatch == null)
                    {
                        passed++;
                        report.Add(rel, index, vectorName, "pass", "");
                    }
                    else
                    {
                        Bump(failures, $"{Path.GetFileName(file)}: {mismatch}");
                        report.Add(rel, index, vectorName, "fail", mismatch);
                    }
                }
                catch (Exception e)
                {
                    Bump(failures, $"{Path.GetFileName(file)}: {e.GetType().Name}: {e.Message}");
                    report.Add(rel, index, vectorName, "error", $"{e.GetType().Name}: {e.Message}");
                }
            }
        }

        _output.WriteLine($"corpus vectors: {total} total, {passed} passed, {total - passed} failed");
        foreach (var detail in failures.Keys.Take(12)) _output.WriteLine("  " + detail);
        if (failures.Count > 12)
            _output.WriteLine($"  ... and {failures.Count - 12} more distinct failures");

        report.Write(_output.WriteLine);
        metaReport.Write(_output.WriteLine);
        if (report.Restricted)
        {
            _output.WriteLine(
                $"CORPUS_ONLY/CORPUS_ROOT is set: {report.RestrictedCount} schema(s) selected, floor {CorpusFloor} not applied");
            return;
        }
        Assert.True(passed >= CorpusFloor,
            $"only {passed} corpus vectors pass, floor is {CorpusFloor}");
    }

    /// <summary>
    /// The input context of a vector (PS-495): its fPort, else input_metadata's, and
    /// input_metadata's recvTime and devEUI. A devEUI that YAML resolves to something
    /// other than a string is passed as written, since the context carries a string.
    /// </summary>
    internal static InputContext MetaContext(YamlMappingNode vector)
    {
        YamlNode? Child(YamlMappingNode map, string key) =>
            map.Children.TryGetValue(new YamlScalarNode(key), out var value) ? value : null;
        var port = Meta.FromYaml(Child(vector, "fPort") ?? Child(vector, "fport"));
        var input = Child(vector, "input_metadata") as YamlMappingNode;
        if (port == null && input != null && Meta.FromYaml(Child(input, "fPort")) is long contextPort)
            port = contextPort;
        object? recvTime = input != null ? Meta.FromYaml(Child(input, "recvTime")) : null;
        string? devEui = null;
        if (input != null && Child(input, "devEUI") is YamlScalarNode euiNode && Meta.FromYaml(euiNode) != null)
            devEui = euiNode.Value;
        return new InputContext(port is long p ? (int)p : null, recvTime, devEui);
    }

    /// <summary>A null `_meta` for each payload vector of a schema that did not load (PS-497).</summary>
    static void MetaSchemaFailed(CorpusMetaReport metaReport, string rel, string text)
    {
        if (!metaReport.Enabled) return;
        try
        {
            var yaml = new YamlStream();
            yaml.Load(new StringReader(text));
            var root = (YamlMappingNode)yaml.Documents[0].RootNode;
            if (!root.Children.TryGetValue(new YamlScalarNode("test_vectors"), out var node)
                || node is not YamlSequenceNode vectors)
                return;
            for (int i = 0; i < vectors.Children.Count; i++)
                if (vectors.Children[i] is YamlMappingNode vector
                    && vector.Children.ContainsKey(new YamlScalarNode("payload")))
                    metaReport.Add(rel, i, CorpusReport.Scalar(vector, "name"), null);
        }
        catch (Exception)
        {
            // Unreadable YAML: the reference skips it too.
        }
    }

    /// <summary>
    /// Whether `_meta` is exactly what a vector expects, or where it differs (CR-2026-096).
    /// Exact, not a subset: the same keys at every level. Numbers compare by value, a
    /// boolean is never a number, and lists compare in order. Mirrors meta_matches in
    /// tools/validate_schema.py.
    /// </summary>
    internal static string? MetaMismatch(object? want, object? got, string path)
    {
        switch (want)
        {
            case Dictionary<string, object?> wantMap:
            {
                if (got is not Dictionary<string, object?> gotMap)
                    return $"{path}: expected an object, got {Meta.PyRepr(got)}";
                var missing = wantMap.Keys.Where(k => !gotMap.ContainsKey(k)).ToList();
                var extra = gotMap.Keys.Where(k => !wantMap.ContainsKey(k)).ToList();
                if (missing.Count > 0 || extra.Count > 0)
                    return $"{path}: missing [{string.Join(", ", missing)}], unexpected [{string.Join(", ", extra)}]";
                foreach (var (key, value) in wantMap)
                    if (MetaMismatch(value, gotMap[key], $"{path}.{key}") is { } detail)
                        return detail;
                return null;
            }
            case List<object?> wantList:
            {
                if (got is not List<object?> gotList || gotList.Count != wantList.Count)
                    return $"{path}: expected {Meta.PyRepr(want)}, got {Meta.PyRepr(got)}";
                for (int i = 0; i < wantList.Count; i++)
                    if (MetaMismatch(wantList[i], gotList[i], $"{path}[{i}]") is { } detail)
                        return detail;
                return null;
            }
        }
        bool ok;
        if (want is bool || got is bool)
            ok = want is bool a && got is bool b && a == b;
        else if (IsMetaNumber(want) && IsMetaNumber(got))
            ok = Convert.ToDouble(want, System.Globalization.CultureInfo.InvariantCulture)
                 == Convert.ToDouble(got, System.Globalization.CultureInfo.InvariantCulture);
        else
            ok = Equals(want, got);
        return ok ? null : $"{path}: expected {Meta.PyRepr(want)}, got {Meta.PyRepr(got)}";
    }

    static bool IsMetaNumber(object? value) => value is long or int or double;

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
    /// <summary>A YAML null: `null`, `~`, or nothing, written plain.</summary>
    static bool IsNull(YamlNode node) => node is YamlScalarNode scalar
        && scalar.Style is YamlDotNet.Core.ScalarStyle.Plain or YamlDotNet.Core.ScalarStyle.Any
        && scalar.Value is null or "" or "~" or "null" or "Null" or "NULL";

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
                // PS-043 as amended (CR-2026-111): null at a position asserts that null was
                // reported there - an `element` repeat's omitted value (PS-528).
                return !seq.Children.Where((child, i) =>
                    IsNull(child) ? gotItems[i] != null : !NodeMatches(child, gotItems[i])).Any();

            case YamlMappingNode map:
                if (got is not System.Collections.IDictionary gotMap) return false;
                foreach (var entry in map.Children)
                {
                    var mapKey = ((YamlScalarNode)entry.Key).Value ?? "";
                    if (IsNull(entry.Value))
                    {
                        if (gotMap.Contains(mapKey)) return false;   // PS-043
                        continue;
                    }
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
        // A string decoded to the very text expected matches, whatever it looks like.
        // "3E6388" - a hex rendering of three bytes - parses as a double overflowing to
        // Infinity, and |Infinity - Infinity| is NaN, so two identical strings failed.
        if (got is string gotText && string.Equals(want.ToString(), gotText, StringComparison.Ordinal))
            return true;
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
        // Text that overflows to an infinity, or spells NaN, is not a number a decode
        // reports (PS-282): compare it as the text it is.
        return double.TryParse(text, System.Globalization.NumberStyles.Any,
            System.Globalization.CultureInfo.InvariantCulture, out var number)
            && double.IsFinite(number)
            ? number
            : null;
    }
}
