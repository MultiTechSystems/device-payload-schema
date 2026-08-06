// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// Canonical modifier order (PS-101 / PS-102).
///
/// Bare mult, div and add apply in the order mult, div, add whatever order the keys
/// appear in. This decoder previously held the source key order in a ModOrder list
/// and applied the modifiers in that order, with a differently-ordered fallback when
/// the list was empty -- so it disagreed with the C and Python interpreters. Nothing
/// covered that, which is why it survived. See CR-2026-002.
/// </summary>
public class CanonicalModifierOrderTests
{
    // raw = 0x0271 = 625. Canonical order gives (625 / 10) - 400 = -337.5.
    const string AddFirst = @"
name: canonical_add_first
endian: big
fields:
  - name: soil_temperature
    type: u16
    add: -400
    div: 10
";

    const string DivFirst = @"
name: canonical_div_first
endian: big
fields:
  - name: soil_temperature
    type: u16
    div: 10
    add: -400
";

    // Applying the offset first requires an explicit ordered transform.
    const string WithTransform = @"
name: transform_order
endian: big
fields:
  - name: soil_temperature
    type: u16
    transform:
      - add: -400
      - div: 10
";

    static double DecodeTemperature(string yaml)
    {
        var schema = SchemaParser.Parse(yaml);
        var result = SchemaDecoder.Decode(schema, new byte[] { 0x02, 0x71 });
        return Convert.ToDouble(result["soil_temperature"]);
    }

    [Theory]
    [InlineData(AddFirst)]
    [InlineData(DivFirst)]
    public void KeyOrderDoesNotChangeTheResult(string yaml)
    {
        Assert.Equal(-337.5, DecodeTemperature(yaml), 3);
    }

    [Fact]
    public void BothKeyOrdersAgree()
    {
        Assert.Equal(DecodeTemperature(AddFirst), DecodeTemperature(DivFirst), 3);
    }

    [Fact]
    public void TransformExpressesOffsetBeforeDivision()
    {
        Assert.Equal(22.5, DecodeTemperature(WithTransform), 3);
    }

    [Fact]
    public void AllThreeModifiersApplyInCanonicalOrder()
    {
        // ((100 * 3) / 2) + 1 = 151
        const string yaml = @"
name: three_modifiers
endian: big
fields:
  - name: v
    type: u16
    add: 1
    div: 2
    mult: 3
";
        var schema = SchemaParser.Parse(yaml);
        var result = SchemaDecoder.Decode(schema, new byte[] { 0x00, 0x64 });
        Assert.Equal(151.0, Convert.ToDouble(result["v"]), 3);
    }
}
