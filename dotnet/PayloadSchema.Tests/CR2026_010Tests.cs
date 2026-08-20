// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-010: a message handled against an entry declared for the other direction is an
/// error, and no field is returned.
///
/// Before this, PortDef.Direction was parsed and never read: DecodeWithPort matched the
/// port number, then the default entry, and decoded whatever it found. An uplink on a
/// port declared <c>direction: downlink</c> came back as command=0,
/// reporting_interval=60225 - three bytes that were a temperature and a humidity. The
/// same message text is asserted in the Python, Go and Java suites.
/// </summary>
public class CR2026_010Tests
{
    const string Ported = @"
name: demo_sensor
endian: big
ports:
  1:
    direction: uplink
    fields:
      - {name: temperature, type: s16, div: 10}
      - {name: humidity, type: u8}
  2:
    direction: downlink
    fields:
      - {name: command, type: u8}
      - {name: reporting_interval, type: u16}
  3:
    direction: both
    fields:
      - {name: x, type: u8}
  4:
    fields:
      - {name: y, type: u8}
";

    static readonly byte[] UplinkPayload = { 0x00, 0xEB, 0x41 };

    static PayloadSchemaDefinition Schema(string body = Ported) => SchemaParser.Parse(body);

    [Theory]
    [InlineData(2, "uplink", "fPort 2 is declared direction:downlink; message direction is uplink")]
    [InlineData(1, "downlink", "fPort 1 is declared direction:uplink; message direction is downlink")]
    public void A_direction_mismatch_is_an_error(int fPort, string direction, string expected)
    {
        var caught = Assert.Throws<InvalidOperationException>(
            () => SchemaDecoder.DecodeWithPort(Schema(), UplinkPayload, fPort, direction));
        Assert.Equal(expected, caught.Message);
    }

    [Fact]
    public void The_declared_direction_still_decodes()
    {
        var up = SchemaDecoder.DecodeWithPort(Schema(), UplinkPayload, 1, "uplink");
        Assert.Equal(23.5, Convert.ToDouble(up["temperature"]));
        Assert.Equal(65, Convert.ToInt32(up["humidity"]));

        var down = SchemaDecoder.DecodeWithPort(Schema(), UplinkPayload, 2, "downlink");
        Assert.Equal(60225, Convert.ToInt32(down["reporting_interval"]));
    }

    [Fact]
    public void An_unstated_direction_decodes_as_before()
    {
        // PS-290: no existing caller changes behaviour.
        var result = SchemaDecoder.DecodeWithPort(Schema(), UplinkPayload, 2);
        Assert.Equal(60225, Convert.ToInt32(result["reporting_interval"]));
    }

    [Theory]
    [InlineData(3)]  // declares `both`
    [InlineData(4)]  // declares nothing, which PS-287 reads as `both`
    public void An_entry_accepting_either_direction_is_not_checked(int fPort)
    {
        foreach (var direction in new[] { "uplink", "downlink" })
        {
            var result = SchemaDecoder.DecodeWithPort(Schema(), new byte[] { 0x2A }, fPort, direction);
            Assert.NotEmpty(result);
        }
    }

    [Fact]
    public void The_default_entry_is_checked_and_named_as_itself()
    {
        // PS-289. Naming fPort 42 would describe a port the schema never defined.
        const string body = @"
name: t
endian: big
ports:
  1:
    direction: uplink
    fields: [{name: x, type: u8}]
  default:
    direction: downlink
    fields: [{name: raw, type: u8}]
";
        var caught = Assert.Throws<InvalidOperationException>(
            () => SchemaDecoder.DecodeWithPort(Schema(body), new byte[] { 0xAB }, 42, "uplink"));
        Assert.Equal(
            "the default port entry is declared direction:downlink; message direction is uplink",
            caught.Message);
    }

    [Fact]
    public void A_schema_level_declaration_is_checked()
    {
        // PS-291: with no ports, the declaration applies to the whole schema.
        const string body = @"
name: cfg
endian: big
direction: downlink
fields: [{name: reporting_interval, type: u16}]
";
        var caught = Assert.Throws<InvalidOperationException>(
            () => SchemaDecoder.DecodeWithPort(Schema(body), new byte[] { 0x00, 0x3C }, 0, "uplink"));
        Assert.Equal(
            "schema 'cfg' is declared direction:downlink; message direction is uplink",
            caught.Message);

        var result = SchemaDecoder.DecodeWithPort(Schema(body), new byte[] { 0x00, 0x3C }, 0, "downlink");
        Assert.Equal(60, Convert.ToInt32(result["reporting_interval"]));
    }

    [Fact]
    public void The_withdrawn_bidirectional_spelling_surfaces()
    {
        const string body = @"
name: t
endian: big
ports:
  7:
    direction: bidirectional
    fields: [{name: x, type: u8}]
";
        var caught = Assert.Throws<InvalidOperationException>(
            () => SchemaDecoder.DecodeWithPort(Schema(body), new byte[] { 0x2A }, 7, "uplink"));
        Assert.Equal(
            "fPort 7 declares unknown direction \"bidirectional\"; expected both, downlink, uplink",
            caught.Message);
    }

    [Fact]
    public void An_unknown_message_direction_is_a_caller_error()
    {
        // A bad argument is a programming error, not a payload problem.
        var caught = Assert.Throws<ArgumentException>(
            () => SchemaDecoder.DecodeWithPort(Schema(), UplinkPayload, 1, "sideways"));
        Assert.Contains("unknown message direction \"sideways\"", caught.Message);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("uplink")]
    [InlineData("downlink")]
    public void An_unmatched_port_stays_an_unmatched_port(string? direction)
    {
        // PS-288 keeps the two faults apart. Running the check first must not turn "the
        // schema does not define this port" into a direction complaint.
        const string body = @"
name: nd
endian: big
ports:
  1:
    direction: uplink
    fields: [{name: x, type: u8}]
";
        var caught = Assert.Throws<InvalidOperationException>(
            () => SchemaDecoder.DecodeWithPort(Schema(body), new byte[] { 0x01 }, 99, direction));
        Assert.Contains("No port definition for fPort 99", caught.Message);
    }

    [Fact]
    public void Encoding_for_a_port_that_disclaims_the_direction_is_an_error()
    {
        // PS-292: emitting the bytes would put a malformed frame on the air.
        var data = new Dictionary<string, object?> { ["temperature"] = 23.5 };
        var caught = Assert.Throws<InvalidOperationException>(
            () => SchemaEncoder.EncodeWithPort(Schema(), data, 1, null, "downlink"));
        Assert.Equal(
            "fPort 1 is declared direction:uplink; message direction is downlink",
            caught.Message);
    }

    [Fact]
    public void Encoding_the_declared_direction_still_works()
    {
        var data = new Dictionary<string, object?>
        {
            ["command"] = 0,
            ["reporting_interval"] = 60,
        };
        var stated = SchemaEncoder.EncodeWithPort(Schema(), data, 2, null, "downlink");
        var unstated = SchemaEncoder.EncodeWithPort(Schema(), data, 2);
        Assert.Equal(new byte[] { 0x00, 0x00, 0x3C }, stated.Payload);
        Assert.Equal(stated.Payload, unstated.Payload);
    }
}
