using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// 0.5.2 wave 6a: the repeat iterator and reserved trailers. CR-2026-048 (reserve,
/// trailer), CR-2026-081 (tlv reserve), CR-2026-053 (index, count_as, present_if),
/// CR-2026-080 (count_as after present_if), CR-2026-055 (carry) and CR-2026-054's identity
/// and per-element annotations. The _language-conformance fixtures hold the decodes; these
/// hold what a vector cannot express - rejections, errors and encoding. Mirrors
/// tests/test_wave6a_repeat_iterator.py.
/// </summary>
public class Wave6aTests
{
    /// <summary>An until-end repeat of one u8 member, with extra keys spliced in.</summary>
    static string Repeat(string extra = "", string fields = "[{\"name\": \"v\", \"type\": \"u8\"}]")
        => "{\"name\": \"probe\", \"fields\": [{\"name\": \"r\", \"type\": \"repeat\", \"until\": \"end\", "
           + (extra.Length > 0 ? extra + ", " : "") + "\"fields\": " + fields + "}]}";

    static Dictionary<string, object?> Decode(string schema, string hex)
        => SchemaDecoder.Decode(SchemaParser.Parse(schema), Convert.FromHexString(hex.Replace(" ", "")));

    public static IEnumerable<object[]> Rejected => new[]
    {
        new object[] { "PS-350", "{\"name\": \"p\", \"fields\": [{\"name\": \"r\", \"type\": \"repeat\", \"count\": 1, "
            + "\"reserve\": 1, \"fields\": [{\"name\": \"v\", \"type\": \"u8\"}]}]}" },
        new object[] { "PS-350", Repeat("\"reserve\": -1") },
        new object[] { "PS-383", Repeat("\"trailer\": [{\"name\": \"t\", \"type\": \"u8\"}]") },
        new object[] { "PS-384", Repeat("\"reserve\": 2, \"trailer\": [{\"name\": \"t\", \"type\": \"u8\"}]") },
        new object[] { "PS-384", Repeat("\"reserve\": 1, \"trailer\": [{\"name\": \"t\", \"type\": \"ascii\", \"length\": \"remaining\"}]") },
        new object[] { "PS-369", Repeat("\"index\": \"v\"") },
        new object[] { "PS-369", Repeat("\"index\": \"k\", \"count_as\": \"k\"") },
        new object[] { "PS-369", "{\"name\": \"p\", \"fields\": [{\"name\": \"outer\", \"type\": \"repeat\", \"count\": 1, "
            + "\"index\": \"i\", \"fields\": [{\"name\": \"inner\", \"type\": \"repeat\", \"count\": 1, \"index\": \"i\", "
            + "\"fields\": [{\"name\": \"v\", \"type\": \"u8\"}]}]}]}" },
        new object[] { "PS-386", Repeat("\"present_if\": {\"field\": \"$v\"}") },
        new object[] { "PS-372", Repeat("\"identity\": \"$nope\"") },
        new object[] { "PS-381", Repeat(fields: "[{\"name\": \"v\", \"type\": \"u8\", \"carry\": 0}]") },
        new object[] { "PS-381", "{\"name\": \"p\", \"fields\": [{\"name\": \"x\", \"type\": \"number\", \"value\": 1, \"carry\": 0}]}" },
        new object[] { "PS-381", Repeat(fields: "[{\"name\": \"v\", \"type\": \"u8\"}, {\"name\": \"t\", \"type\": \"number\", "
            + "\"carry\": \"zero\", \"compute\": {\"op\": \"add\", \"a\": \"$t\", \"b\": \"$v\"}}]") },
        new object[] { "PS-374", Repeat(fields: "[{\"name\": \"code\", \"type\": \"u8\"}, "
            + "{\"name\": \"v\", \"type\": \"u8\", \"unit\": \"$code\"}]") },
        new object[] { "PS-374", Repeat("\"index\": \"i\"", "[{\"name\": \"v\", \"type\": \"u8\", "
            + "\"ipso\": {\"object\": 3303, \"instance\": \"$i\"}}]") },
        new object[] { "PS-373", Repeat(fields: "[{\"name\": \"v\", \"type\": \"u8\", \"senml\": {\"name\": \"t_${nope}\"}}]") },
        new object[] { "PS-471", "{\"name\": \"p\", \"fields\": [{\"tlv\": {\"tag_size\": 1, \"reserve\": -1, "
            + "\"cases\": {\"1\": [{\"name\": \"a\", \"type\": \"u8\"}]}}}]}" },
    };

    [Theory]
    [MemberData(nameof(Rejected))]
    public void ASchemaRuleIsEnforcedAtLoad(string tag, string schema)
    {
        var e = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(schema));
        Assert.Contains(tag, e.Message);
    }

    [Fact]
    public void AValidIdentitySchemaIsAccepted()
    {
        var schema = Repeat("\"index\": \"i\", \"max\": 4, \"identity\": \"i\"",
            "[{\"name\": \"code\", \"type\": \"u8\", \"lookup\": {\"1\": \"Cel\", \"2\": \"%RH\"}}, "
            + "{\"name\": \"v\", \"type\": \"u8\", \"unit\": \"$code\", "
            + "\"ipso\": {\"object\": 3303, \"instance\": \"$i\"}, \"senml\": {\"name\": \"t_${i}\"}}]");
        var output = Decode(schema, "0102");
        // PS-376: the annotations change no decode output.
        var r = Assert.IsType<List<object?>>(output["r"]);
        var element = Assert.IsType<Dictionary<string, object?>>(Assert.Single(r));
        Assert.Equal("Cel", element["code"]);
        Assert.Equal(2UL, element["v"]);
    }

    // PS-351, PS-471: too few bytes for the reserve ----------------------------------

    [Fact]
    public void TooFewBytesForARepeatReserveIsAnError()
    {
        var e = Assert.Throws<InvalidOperationException>(() => Decode(Repeat("\"reserve\": 2"), "01"));
        Assert.Contains("PS-351", e.Message);
    }

    [Fact]
    public void TooFewBytesForATrailerIsAnError()
    {
        var schema = Repeat("\"reserve\": 2, \"trailer\": [{\"name\": \"t\", \"type\": \"u16\"}]");
        var e = Assert.Throws<InvalidOperationException>(() => Decode(schema, "01"));
        Assert.Contains("PS-351", e.Message);
    }

    [Fact]
    public void TooFewBytesForATlvReserveIsAnError()
    {
        const string schema = "{\"name\": \"p\", \"fields\": [{\"tlv\": {\"tag_size\": 1, \"reserve\": 2, "
            + "\"cases\": {\"1\": [{\"name\": \"a\", \"type\": \"u8\"}]}}}]}";
        var e = Assert.Throws<InvalidOperationException>(() => Decode(schema, "01"));
        Assert.Contains("PS-471", e.Message);
    }

    [Fact]
    public void ARaggedTailIsMeasuredAgainstTheRegion()
    {
        // PS-350: the ragged-tail rule applies to the payload less the reserved bytes.
        var schema = Repeat("\"reserve\": 1", "[{\"name\": \"v\", \"type\": \"u16\"}]");
        Assert.Single(Assert.IsType<List<object?>>(Decode(schema, "000102")["r"]));
        var e = Assert.Throws<InvalidOperationException>(() => Decode(schema, "00010203"));
        Assert.Contains("ragged", e.Message);
    }

    // PS-368: element names are scoped -----------------------------------------------

    [Fact]
    public void AReferenceFromOutsideToAnElementNameIsAnError()
    {
        const string schema = "{\"name\": \"p\", \"fields\": ["
            + "{\"name\": \"r\", \"type\": \"repeat\", \"count\": 2, \"fields\": [{\"name\": \"v\", \"type\": \"u8\"}]}, "
            + "{\"name\": \"last\", \"type\": \"number\", \"ref\": \"$v\"}]}";
        var e = Assert.Throws<InvalidOperationException>(() => Decode(schema, "0102"));
        Assert.Contains("PS-368", e.Message);
    }

    [Fact]
    public void AReferenceToAFieldNotYetDecodedIsAnError()
    {
        const string schema = "{\"name\": \"p\", \"fields\": [{\"name\": \"r\", \"type\": \"repeat\", \"count\": 1, "
            + "\"fields\": [{\"name\": \"early\", \"type\": \"number\", \"ref\": \"$v\"}, {\"name\": \"v\", \"type\": \"u8\"}]}]}";
        var e = Assert.Throws<InvalidOperationException>(() => Decode(schema, "05"));
        Assert.Contains("PS-368", e.Message);
    }

    [Fact]
    public void NeitherIndexNorCountAsIsReported()
    {
        // PS-370.
        var output = Decode(Repeat("\"index\": \"i\", \"count_as\": \"n\""), "0102");
        Assert.Equal(new[] { "r" }, output.Keys.ToArray());
        var r = Assert.IsType<List<object?>>(output["r"]);
        Assert.Equal(2, r.Count);
        foreach (var element in r)
            Assert.Equal(new[] { "v" }, Assert.IsType<Dictionary<string, object?>>(element).Keys.ToArray());
    }

    [Fact]
    public void CarryFromAFieldDecodedBeforeTheRepeat()
    {
        // PS-378: carry may name a field decoded before the repeat.
        const string schema = "{\"name\": \"p\", \"fields\": [{\"name\": \"start\", \"type\": \"u8\"}, "
            + "{\"name\": \"r\", \"type\": \"repeat\", \"count\": 2, \"fields\": [{\"name\": \"step\", \"type\": \"u8\"}, "
            + "{\"name\": \"level\", \"type\": \"number\", \"carry\": \"$start\", "
            + "\"compute\": {\"op\": \"add\", \"a\": \"$level\", \"b\": \"$step\"}}]}]}";
        var r = Assert.IsType<List<object?>>(Decode(schema, "640105")["r"]);
        Assert.Equal(101.0, ((Dictionary<string, object?>)r[0]!)["level"]);
        Assert.Equal(106.0, ((Dictionary<string, object?>)r[1]!)["level"]);
    }

    // Encoding: PS-370, PS-385, PS-387 ------------------------------------------------

    [Fact]
    public void TheTrailerIsWrittenAfterTheElements()
    {
        var schema = SchemaParser.Parse(Repeat("\"reserve\": 1, \"trailer\": [{\"name\": \"base\", \"type\": \"u8\"}]"));
        var result = SchemaEncoder.Encode(schema, new Dictionary<string, object?>
        {
            ["r"] = new List<object?>
            {
                new Dictionary<string, object?> { ["v"] = 1 },
                new Dictionary<string, object?> { ["v"] = 2 },
            },
            ["base"] = 9,
        });
        Assert.Empty(result.Errors);
        Assert.Equal(new byte[] { 1, 2, 9 }, result.Payload);

        // And the decode reads the trailer from the end, reporting it beside the repeat.
        var output = SchemaDecoder.Decode(schema, result.Payload);
        Assert.Equal(9UL, output["base"]);
        Assert.Equal(2, Assert.IsType<List<object?>>(output["r"]).Count);
    }

    [Fact]
    public void TheIndexIsBoundWhileEncoding()
    {
        // PS-370: a name_from template resolves on encode as it did on decode.
        var schema = SchemaParser.Parse("{\"name\": \"p\", \"fields\": [{\"name\": \"r\", \"type\": \"repeat\", "
            + "\"count\": 2, \"index\": \"i\", \"fields\": [{\"name\": \"v\", \"type\": \"u8\", \"name_from\": \"ch_${i}\"}]}]}");
        var payload = new byte[] { 0x07, 0x08 };
        var decoded = SchemaDecoder.Decode(schema, payload);
        var r = Assert.IsType<List<object?>>(decoded["r"]);
        Assert.Equal(7UL, Assert.IsType<Dictionary<string, object?>>(r[0]).Single(kv => kv.Key == "ch_0").Value);
        Assert.Equal(8UL, Assert.IsType<Dictionary<string, object?>>(r[1]).Single(kv => kv.Key == "ch_1").Value);
        Assert.Single((Dictionary<string, object?>)r[0]!);
        Assert.Single((Dictionary<string, object?>)r[1]!);
        var back = SchemaEncoder.Encode(schema, decoded);
        Assert.Empty(back.Errors);
        Assert.Equal(payload, back.Payload);
    }

    [Fact]
    public void PresentIfOverByteReadingElementsCannotBeEncoded()
    {
        var schema = SchemaParser.Parse(Repeat("\"present_if\": {\"field\": \"$v\", \"ne\": 0}"));
        var result = SchemaEncoder.Encode(schema, new Dictionary<string, object?>
        {
            ["r"] = new List<object?> { new Dictionary<string, object?> { ["v"] = 1 } },
        });
        Assert.False(result.Success);
        Assert.Contains(result.Errors, e => e.Contains("PS-387"));
    }

    [Fact]
    public void PresentIfDropsAnElementButNotItsIndexOrBytes()
    {
        // PS-386 and CR-2026-080: count_as counts the elements reported.
        const string schema = "{\"name\": \"p\", \"fields\": [{\"name\": \"s\", \"type\": \"repeat\", \"count\": 3, "
            + "\"index\": \"i\", \"count_as\": \"n\", \"present_if\": {\"field\": \"$id\", \"ne\": 0}, "
            + "\"fields\": [{\"name\": \"id\", \"type\": \"u8\"}, {\"name\": \"at\", \"type\": \"number\", \"ref\": \"$i\"}]}, "
            + "{\"name\": \"kept\", \"type\": \"number\", \"ref\": \"$n\"}, {\"name\": \"tail\", \"type\": \"u8\"}]}";
        var output = Decode(schema, "00 04 00 09");
        var s = Assert.IsType<List<object?>>(output["s"]);
        var only = Assert.IsType<Dictionary<string, object?>>(Assert.Single(s));
        Assert.Equal(4UL, only["id"]);
        Assert.Equal(1.0, only["at"]);
        Assert.Equal(1.0, output["kept"]);
        Assert.Equal(9UL, output["tail"]);
    }
}
