// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// `_meta` in the interpreter output: CR-2026-096 (PS-490 to PS-497), with PS-175 to
/// PS-181, PS-340 to PS-342, PS-371 to PS-376, CR-2026-088's PS-480/481 and CR-2026-095's
/// PS-489. Mirrors tests/test_cr_2026_096_meta.py; the `_language-conformance/meta-*.yaml`
/// fixtures and the corpus runner's `expected_meta` cover the output itself.
/// </summary>
public class CR2026_096MetaTests
{
    const string Flat = "name: flat\nfields:\n- {name: a, type: u8, unit: V}\n- {name: b, type: u8}\n";

    static readonly byte[] TwoBytes = { 0x01, 0x02 };

    static Dictionary<string, object?> MetaOf(Dictionary<string, object?> output) =>
        (Dictionary<string, object?>)output["_meta"]!;

    [Fact]
    public void DecodeIsUnchanged()
    {
        // td-tools-style callers read Decode; _meta comes only from Interpret.
        var schema = SchemaParser.Parse(Flat);
        var decoded = SchemaDecoder.Decode(schema, TwoBytes);
        Assert.Equal(new[] { "a", "b" }, decoded.Keys);
        Assert.Contains("_meta", SchemaDecoder.Interpret(schema, TwoBytes, new InputContext()).Keys);
    }

    [Fact]
    public void MetaIsLastAndPresentWithoutAnnotations()
    {
        // PS-180: produced with every decoded result, whether or not the schema annotates.
        var output = SchemaDecoder.Interpret(SchemaParser.Parse(Flat), TwoBytes, new InputContext());
        Assert.Equal("_meta", output.Keys.Last());
        var want = new Dictionary<string, object?>
        {
            ["schema"] = "flat",
            ["fields"] = new Dictionary<string, object?>
            {
                ["a"] = new Dictionary<string, object?> { ["type"] = "u8", ["unit"] = "V" },
                ["b"] = new Dictionary<string, object?> { ["type"] = "u8" },
            },
        };
        Assert.Null(CorpusConformanceTests.MetaMismatch(want, output["_meta"], "_meta"));
    }

    [Fact]
    public void AFailedDecodeHasNoMeta()
    {
        // PS-497: Interpret fails exactly as the decode does.
        Assert.Throws<InvalidOperationException>(() =>
            SchemaDecoder.Interpret(SchemaParser.Parse(Flat), new byte[] { 0x01 }, new InputContext()));
    }

    [Fact]
    public void FPortComesFromTheContextAndIsOmittedWithout()
    {
        var schema = SchemaParser.Parse(Flat);
        Assert.Equal(5L, MetaOf(SchemaDecoder.Interpret(schema, TwoBytes, new InputContext(FPort: 5)))["fPort"]);
        Assert.False(MetaOf(SchemaDecoder.Interpret(schema, TwoBytes, new InputContext())).ContainsKey("fPort"));  // PS-341
    }

    [Fact]
    public void TheRunnerTakesFPortFromInputMetadataWhereTheVectorHasNone()
    {
        static YamlDotNet.RepresentationModel.YamlMappingNode Vector(string text)
        {
            var yaml = new YamlDotNet.RepresentationModel.YamlStream();
            yaml.Load(new StringReader(text));
            return (YamlDotNet.RepresentationModel.YamlMappingNode)yaml.Documents[0].RootNode;
        }
        Assert.Equal(6, CorpusConformanceTests.MetaContext(Vector("{payload: '0102', input_metadata: {fPort: 6}}")).FPort);
        Assert.Equal(5, CorpusConformanceTests.MetaContext(Vector("{payload: '0102', fPort: 5, input_metadata: {fPort: 6}}")).FPort);
        Assert.Null(CorpusConformanceTests.MetaContext(Vector("{payload: '0102'}")).FPort);
    }

    [Fact]
    public void AFallThroughToTheDefaultPortReportsTheReceivedFPort()
    {
        // PS-340, PS-342.
        var schema = SchemaParser.Parse("name: p\nports:\n  1:\n    fields: [{name: battery, type: u16}]\n"
            + "  default:\n    fields: [{name: level, type: u8, unit: '/100'}]\n");
        var meta = MetaOf(SchemaDecoder.Interpret(schema, new byte[] { 0x5A }, new InputContext(FPort: 9)));
        Assert.Equal(9L, meta["fPort"]);
        Assert.Equal("/100", ((Dictionary<string, object?>)((Dictionary<string, object?>)meta["fields"]!)["level"]!)["unit"]);
    }

    [Theory]
    [InlineData("0011223344556677", "0011223344556677")]
    [InlineData("00-11-22-33-44-55-66-AA", "00112233445566aa")]
    [InlineData("00:11:22:33:44:55:66:AA", "00112233445566aa")]
    [InlineData("00 11 22 33 44 55 66 aa", "00112233445566aa")]
    [InlineData("0011", null)]
    [InlineData("00112233445566GG", null)]
    public void DevEuiIsNormalised(string text, string? eui)
    {
        Assert.Equal(eui, Meta.NormaliseDevEui(text));    // PS-496
    }

    [Theory]
    [InlineData(null, "nonsense", "devEUI 'nonsense' is not 16 hexadecimal digits (PS-496)")]
    [InlineData("yesterday", null,
        "recvTime 'yesterday' is not an ISO 8601 time or a number of seconds (PS-495)")]
    public void AMalformedContextFailsWithNoMeta(string? recvTime, string? devEui, string message)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaDecoder.Interpret(
            SchemaParser.Parse(Flat), TwoBytes, new InputContext(RecvTime: recvTime, DevEUI: devEui)));
        Assert.Equal(message, thrown.Message);
    }

    [Fact]
    public void ABooleanRecvTimeIsRefused()
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaDecoder.Interpret(
            SchemaParser.Parse(Flat), TwoBytes, new InputContext(RecvTime: true)));
        Assert.Equal("recvTime True is not an ISO 8601 time or a number of seconds (PS-495)", thrown.Message);
    }

    public static IEnumerable<object?[]> RecvTimes() => new[]
    {
        new object?[] { "2026-08-26T12:00:00Z", 1787745600L },
        new object?[] { "2026-08-26T12:00:00.123Z", 1787745600.123 },
        new object?[] { "2026-08-26T12:00:00.120000Z", 1787745600.12 },
        new object?[] { "2026-08-26T12:00:00.1234Z", 1787745600.123 },
        new object?[] { "2026-08-26T12:00:00.1235Z", 1787745600.124 },
        new object?[] { "2026-08-26T12:00:00.1225Z", 1787745600.122 },
        new object?[] { "2026-08-26T12:00:00.9996Z", 1787745601L },
        new object?[] { "2026-08-26T12:00:00.000Z", 1787745600L },
        new object?[] { "2026-08-26T14:00:00+02:00", 1787745600L },
        new object?[] { "2026-08-26T07:30:00-0430", 1787745600L },
        new object?[] { "2026-08-26T12:00:00", 1787745600L },
        new object?[] { 1787745600.5, 1787745600.5 },
        new object?[] { "yesterday", null },
        new object?[] { true, null },
    };

    [Theory]
    [MemberData(nameof(RecvTimes))]
    public void RecvTimeIsUnixSeconds(object recv, object? seconds)
    {
        var got = Meta.RxTimeSeconds(recv);       // PS-177, PS-495
        Assert.Equal(seconds, got);
        Assert.Equal(seconds?.GetType(), got?.GetType());
    }

    static string Disagreeing(string second) =>
        "name: x\nfields:\n- {name: _k, type: u8}\n- match:\n    field: $_k\n    cases:\n"
        + "      1: [{name: r, type: u8, unit: Cel}]\n"
        + $"      2: [{{name: r, {second}}}]\n";

    [Theory]
    [InlineData("type: s16, unit: Cel")]
    [InlineData("type: u8, unit: Cel, description: other")]
    public void DeclarationsMayDifferInTypeAndDescription(string second)
    {
        var schema = SchemaParser.Parse(Disagreeing(second));
        Assert.Equal(5L, Convert.ToInt64(SchemaDecoder.Decode(schema, new byte[] { 0x01, 0x05 })["r"]));
    }

    [Theory]
    [InlineData("type: u8, unit: K", "unit")]
    [InlineData("type: u8, unit: Cel, senml: {name: t}", "senml")]
    [InlineData("type: u8, unit: Cel, ipso: {object: 3303}", "ipso")]
    public void DeclarationsThatDisagreeOnMeaningAreRejected(string second, string component)
    {
        // PS-491: unit, senml and ipso must agree.
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(Disagreeing(second)));
        Assert.Equal($"Field 'r' is declared 2 times in fields and its declarations differ in {component}; "
            + "the declarations of one reported name must agree on unit, senml and ipso (PS-491)", thrown.Message);
    }

    [Fact]
    public void NamesOnDifferentPortsNeedNotAgree()
    {
        // PS-491's scope is one field list.
        SchemaParser.Parse("name: x\nports:\n  1:\n    fields: [{name: r, type: u8, unit: Cel}]\n"
            + "  2:\n    fields: [{name: r, type: u8, unit: K}]\n");
    }

    [Fact]
    public void OnePortIsOneFieldList()
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(
            "name: x\nports:\n  3:\n    fields:\n    - {name: r, type: u8, unit: Cel}\n"
            + "    - {name: r, type: u8, unit: K}\n"));
        Assert.StartsWith("Field 'r' is declared 2 times in port 3 and its declarations differ in unit;", thrown.Message);
    }

    [Fact]
    public void ANestedListIsItsOwnScope()
    {
        // An object's members are one list, named after the enclosing one.
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(
            "name: x\nfields:\n- name: o\n  type: object\n  fields:\n"
            + "  - {name: r, type: u8, unit: Cel}\n  - {name: r, type: u8, unit: K}\n"));
        Assert.StartsWith("Field 'r' is declared 2 times in fields/o and", thrown.Message);
        // ...and a top-level name may differ from a nested one.
        SchemaParser.Parse("name: x\nfields:\n- {name: r, type: u8, unit: K}\n- name: o\n  type: object\n"
            + "  fields:\n  - {name: r, type: u8, unit: Cel}\n");
    }

    [Fact]
    public void TheProducingDeclarationDescribesTheKey()
    {
        // PS-490: last write wins, from the case that decoded.
        var schema = SchemaParser.Parse(Disagreeing("type: s16, unit: Cel, description: two"));
        var fields = (Dictionary<string, object?>)MetaOf(
            SchemaDecoder.Interpret(schema, new byte[] { 0x02, 0xFF, 0x9C }, new InputContext()))["fields"]!;
        var entry = (Dictionary<string, object?>)fields["r"]!;
        Assert.Equal("s16", entry["type"]);
        Assert.Equal("two", entry["description"]);
    }

    [Fact]
    public void MetaMismatchIsExact()
    {
        var one = new Dictionary<string, object?> { ["a"] = 1L };
        Assert.Null(CorpusConformanceTests.MetaMismatch(one, new Dictionary<string, object?> { ["a"] = 1.0 }, "_meta"));
        Assert.NotNull(CorpusConformanceTests.MetaMismatch(one,
            new Dictionary<string, object?> { ["a"] = 1L, ["b"] = 2L }, "_meta"));
        Assert.NotNull(CorpusConformanceTests.MetaMismatch(
            new Dictionary<string, object?> { ["a"] = 1L, ["b"] = 2L }, one, "_meta"));
        Assert.NotNull(CorpusConformanceTests.MetaMismatch(
            new Dictionary<string, object?> { ["a"] = true }, one, "_meta"));
        Assert.NotNull(CorpusConformanceTests.MetaMismatch(
            new List<object?> { 1L, 2L }, new List<object?> { 2L, 1L }, "_meta"));
        var detail = CorpusConformanceTests.MetaMismatch(
            Meta.FromYaml(Node("{f: {x: {type: u8}}}")), Meta.FromYaml(Node("{f: {x: {type: s8}}}")), "_meta");
        Assert.Contains("_meta.f.x.type", detail);
    }

    static YamlDotNet.RepresentationModel.YamlNode Node(string text)
    {
        var yaml = new YamlDotNet.RepresentationModel.YamlStream();
        yaml.Load(new StringReader(text));
        return yaml.Documents[0].RootNode;
    }

    [Fact]
    public void AWrongExpectationFailsTheFixture()
    {
        // The runner's comparison is not vacuous: meta-nested's expectation, perturbed, fails.
        var path = FindFixture("meta-nested.yaml");
        if (path == null) return;
        var text = File.ReadAllText(path);
        var root = (YamlDotNet.RepresentationModel.YamlMappingNode)Node(text);
        var vector = (YamlDotNet.RepresentationModel.YamlMappingNode)
            ((YamlDotNet.RepresentationModel.YamlSequenceNode)root.Children[new YamlDotNet.RepresentationModel.YamlScalarNode("test_vectors")]).Children[0];
        var actual = MetaOf(SchemaDecoder.Interpret(SchemaParser.Parse(text),
            Convert.FromHexString(((YamlDotNet.RepresentationModel.YamlScalarNode)vector.Children[
                new YamlDotNet.RepresentationModel.YamlScalarNode("payload")]).Value!.Replace(" ", "")),
            new InputContext()));
        var expected = (Dictionary<string, object?>)Meta.FromYaml(
            vector.Children[new YamlDotNet.RepresentationModel.YamlScalarNode("expected_meta")])!;
        Assert.Null(CorpusConformanceTests.MetaMismatch(expected, actual, "_meta"));
        var channels = (Dictionary<string, object?>)((Dictionary<string, object?>)expected["fields"]!)["channels"]!;
        ((Dictionary<string, object?>)channels["identity"]!).Remove("open");
        Assert.NotNull(CorpusConformanceTests.MetaMismatch(expected, actual, "_meta"));
    }

    static string? FindFixture(string name)
    {
        var dir = AppContext.BaseDirectory;
        for (int i = 0; i < 10 && dir != null; i++)
        {
            var candidate = Path.Combine(dir, "schemas", "devices", "_language-conformance", name);
            if (File.Exists(candidate)) return candidate;
            dir = Path.GetDirectoryName(dir);
        }
        return null;
    }

    [Fact]
    public void AFieldWithNoTypeHasNoTypeInItsEntry()
    {
        // PS-011, PS-441: no type is invented. A field with no type is refused at load, so
        // this reaches FieldMeta directly; a named merge:false tlv is still `tlv`.
        var entry = Meta.FieldMeta((YamlDotNet.RepresentationModel.YamlMappingNode)Node("{name: r, unit: V}"));
        Assert.False(entry.ContainsKey("type"));
        Assert.Equal("tlv", Meta.MetaType((YamlDotNet.RepresentationModel.YamlMappingNode)Node(
            "{name: c, tlv: {merge: false, cases: {}}}")));
    }
}
