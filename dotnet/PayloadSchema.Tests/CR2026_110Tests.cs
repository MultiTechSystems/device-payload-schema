// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-110: <c>on_repeat</c> combines a name reported by more than one tlv entry
/// (PS-157, PS-525 to PS-527). Mirrors tests/test_cr_2026_110_on_repeat.py, whose cases are
/// the spec prototype's; the messages are its wording.
/// </summary>
public class CR2026_110Tests
{
    const string Loramac = "name: t\nendian: big\nfields:\n- tlv:\n    tag_size: 2\n    on_repeat: merge\n"
        + "    cases:\n"
        + "      0x0310: [{name: loramac, type: object, fields: [{name: adr, type: u8}]}]\n"
        + "      0x0311: [{name: loramac, type: object, fields: [{name: dr, type: u8}]}]\n"
        + "      0x0001: [{name: battery, type: u8}]\n";

    static string Event(string onRepeat) => "name: t\nendian: big\nfields:\n- tlv:\n    tag_size: 1\n"
        + onRepeat + "    cases:\n"
        + "      0x3a: [{name: event, type: u8}]\n      0x40: [{name: event, type: u8}]\n";

    static Dictionary<string, object?> Decode(string yaml, string hex)
        => SchemaDecoder.Decode(SchemaParser.Parse(yaml), Convert.FromHexString(hex));

    [Fact]
    public void MergeCombinesObjectsAndTheDefaultSparesOtherFields()
    {
        var output = Decode(Loramac, "031001031105000102000103");
        var loramac = Assert.IsType<Dictionary<string, object?>>(output["loramac"]);
        Assert.Equal(new[] { "adr", "dr" }, loramac.Keys.ToArray());
        Assert.Equal(1L, Convert.ToInt64(loramac["adr"]));
        Assert.Equal(5L, Convert.ToInt64(loramac["dr"]));
        var battery = Assert.IsType<List<object?>>(output["battery"]);
        Assert.Equal(new long[] { 2, 3 }, battery.Select(Convert.ToInt64).ToArray());
    }

    [Fact]
    public void AMemberReportedTwiceIsAnError()
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => Decode(Loramac, "031001031002"));
        Assert.Equal("tlv: the merged object 'loramac' reports its member 'adr' twice (PS-525)",
            thrown.Message);
    }

    [Fact]
    public void LastKeepsTheLastValue()
        => Assert.Equal(2L, Convert.ToInt64(Decode(Event("    on_repeat: last\n"), "3a014002")["event"]));

    [Fact]
    public void CollectIsTheDefaultAcrossDifferentTags()
    {
        var events = Assert.IsType<List<object?>>(Decode(Event(""), "3a014002")["event"]);
        Assert.Equal(new long[] { 1, 2 }, events.Select(Convert.ToInt64).ToArray());
    }

    [Fact]
    public void ACollectedListValueIsAnElementNotSpliced()
    {
        // A repeat's value is itself a list. Its second report was appended into the
        // first's elements, giving [{v:1}, {v:2}, [{v:3}, {v:4}]].
        var output = Decode("name: t\nfields:\n- tlv:\n    tag_size: 1\n    cases:\n"
            + "      1: [{name: vals, type: repeat, count: 2, fields: [{name: v, type: u8}]}]\n",
            "010102010304");
        var vals = Assert.IsType<List<object?>>(output["vals"]);
        Assert.Equal(2, vals.Count);
        Assert.All(vals, element => Assert.Equal(2, Assert.IsAssignableFrom<System.Collections.IList>(element).Count));
    }

    [Fact]
    public void MergeEncodesOneEntryPerCase()
    {
        var data = new Dictionary<string, object?>
        {
            ["loramac"] = new Dictionary<string, object?> { ["dr"] = 5L, ["adr"] = 1L },
        };
        var result = SchemaEncoder.Encode(SchemaParser.Parse(Loramac), data);
        Assert.Empty(result.Errors);
        Assert.Equal(Convert.FromHexString("031001031105"), result.Payload);
    }

    [Fact]
    public void LastEncodesOnceUnderTheFirstCase()
    {
        var result = SchemaEncoder.Encode(SchemaParser.Parse(Event("    on_repeat: last\n")),
            new Dictionary<string, object?> { ["event"] = 2L });
        Assert.Empty(result.Errors);
        Assert.Equal(Convert.FromHexString("3a02"), result.Payload);
    }

    [Theory]
    [InlineData("    cases:\n      1: [{name: e, type: u8, on_repeat: merge}]\n",
        "Field 'e': only a `type: object` field may declare `on_repeat: merge` (PS-526)")]
    [InlineData("    cases:\n      1: [{name: e, type: u8, on_repeat: last}]\n      2: [{name: e, type: u8}]\n",
        "tlv: the fields reporting 'e' declare different `on_repeat`, last and collect (PS-526)")]
    [InlineData("    on_repeat: first\n    cases:\n      1: [{name: e, type: u8}]\n",
        "tlv `on_repeat` is one of collect, merge, last, not 'first' (PS-525)")]
    [InlineData("    cases:\n      1: [{name: e, type: u8, on_repeat: keep}]\n",
        "Field 'e': `on_repeat` is one of collect, merge, last, not 'keep' (PS-525)")]
    public void RejectedAtLoad(string body, string message)
    {
        var thrown = Assert.Throws<InvalidOperationException>(
            () => SchemaParser.Parse("name: t\nfields:\n- tlv:\n    tag_size: 1\n" + body));
        Assert.Equal(message, thrown.Message);
    }

    [Fact]
    public void MergeOnATlvSparesAScalar() => SchemaParser.Parse(Loramac);

    // CR-2026-125 and CR-2026-128 (PS-525 to PS-527): the spec prototype's
    // TestCR2026125OnRepeatEncoding and TestCR2026128FirstCaseThatFits.

    const string LoramacShared = "name: t\nendian: big\nfields:\n- tlv:\n    tag_size: 2\n    on_repeat: merge\n"
        + "    cases:\n"
        + "      0x0310: [{name: loramac, type: object, fields: [{name: adr, type: u8}, {name: dr, type: u8, on_repeat: last}]}]\n"
        + "      0x0311: [{name: loramac, type: object, fields: [{name: dr, type: u8, on_repeat: last}]}]\n";
    const string Collected = "name: t\nendian: big\nfields:\n- tlv:\n    tag_size: 2\n    cases:\n"
        + "      0x0310: [{name: mac, type: object, fields: [{name: adr, type: u8}]}]\n"
        + "      0x0311: [{name: mac, type: object, fields: [{name: dr, type: u8}]}]\n";
    const string SharedLast = "name: t\nendian: big\nfields:\n- tlv:\n    tag_size: 1\n    on_repeat: merge\n    cases:\n"
        + "      1: [{name: o, type: object, fields: [{name: a, type: u8}, {name: m, type: u8, on_repeat: last}]}]\n"
        + "      2: [{name: o, type: object, fields: [{name: b, type: u8}, {name: m, type: u8, on_repeat: last}]}]\n";

    static Dictionary<string, object?> D(params (string k, object? v)[] kv)
        => kv.ToDictionary(p => p.k, p => p.v);

    static void EncodesTo(string yaml, Dictionary<string, object?> data, string want)
    {
        var result = SchemaEncoder.Encode(SchemaParser.Parse(yaml), data);
        Assert.Empty(result.Errors);
        Assert.Equal(Convert.FromHexString(want), result.Payload);
    }

    [Fact]
    public void EachValueGoesUnderTheFirstCaseItFills()
    {
        EncodesTo(Event(""), D(("event", new List<object?> { 1L, 2L })), "3a013a02");
        EncodesTo(Event(""), D(("event", 3L)), "3a03");
        EncodesTo(Collected, D(("mac", new List<object?> { D(("adr", 1L)), D(("dr", 5L)) })), "031001031105");
        EncodesTo(LoramacShared, D(("loramac", D(("dr", 5L)))), "031105");
        EncodesTo(LoramacShared, D(("loramac", D(("adr", 1L), ("dr", 5L)))), "03100105");
        EncodesTo(SharedLast, D(("o", D(("a", 1L), ("b", 2L), ("m", 9L)))), "010109020209");
    }

    [Fact]
    public void AValueNoCaseTakesInFullIsAnError()
    {
        var result = SchemaEncoder.Encode(SchemaParser.Parse(LoramacShared),
            D(("loramac", D(("adr", 1L), ("dr", 5L), ("fw", 3L)))));
        Assert.Contains(result.Errors, e => e.Contains(
            "tlv: the merged object 'loramac' is given ['fw'], which no case declares (PS-527)"));
        result = SchemaEncoder.Encode(SchemaParser.Parse(Collected),
            D(("mac", new List<object?> { D(("adr", 1L), ("dr", 5L)) })));
        Assert.Contains(result.Errors, e => e.Contains("tlv: no case takes the value of 'mac'"));
    }

    [Fact]
    public void ASharedMemberWithoutLastIsRejectedAtLoad()
    {
        var e = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(LoramacShared.Replace(", on_repeat: last", "")));
        Assert.Equal("tlv: cases share the member 'dr' of the merged object 'loramac' without `on_repeat: last` (PS-526)",
            e.Message);
    }

    [Theory]
    [InlineData("- {name: a, type: u8, on_repeat: last}\n", "a")]
    [InlineData("- tlv:\n    cases:\n      1: [{name: o, type: object, fields: [{name: m, type: u8, on_repeat: last}]}]\n", "m")]
    public void OnRepeatWhereItMeansNothingIsRejected(string fields, string name)
    {
        var e = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse("name: t\nfields:\n" + fields));
        Assert.Equal($"`on_repeat` on '{name}' has no effect there: it belongs on a tlv, a field of a "
            + "tlv case, or a merged object's member (PS-525)", e.Message);
    }

    [Fact]
    public void LastOnAMergedMemberIsAccepted()
    {
        var out_ = Decode("name: t\nendian: big\nfields:\n- tlv:\n    on_repeat: merge\n    cases:\n"
            + "      1: [{name: o, type: object, fields: [{name: m, type: u8, on_repeat: last}]}]\n", "01050107");
        var o = Assert.IsType<Dictionary<string, object?>>(out_["o"]);
        Assert.Equal(7.0, Convert.ToDouble(o["m"]));
    }

    /// <summary>CR-2026-129: an object case is as wide as its members.</summary>
    [Fact]
    public void AnObjectCaseIsAsWideAsItsMembers()
    {
        const string wider = "name: t\nendian: big\nfields:\n- tlv:\n    tag_size: 2\n    on_repeat: merge\n    cases:\n"
            + "      0x0310: [{name: loramac, type: object, fields: [{name: dr, type: u8, on_repeat: last}]}]\n"
            + "      0x0311: [{name: loramac, type: object, fields: [{name: adr, type: u8}, {name: dr, type: u8, on_repeat: last}]}]\n";
        EncodesTo(wider, D(("loramac", D(("adr", 1L), ("dr", 5L)))), "03110105");
    }
}
