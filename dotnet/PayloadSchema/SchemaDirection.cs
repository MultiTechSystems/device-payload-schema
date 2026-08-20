// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

namespace PayloadSchema;

/// <summary>
/// The direction check from CR-2026-010, shared by the decoder and the encoder.
///
/// A port entry declaring <c>direction</c> states which way traffic on that port runs, so
/// a message travelling the other way does not match the schema (PS-021). Before this,
/// <c>PortDef.Direction</c> was parsed and never read: an uplink on a port declared
/// <c>direction: downlink</c> decoded against that port's fields and came back as
/// command=0, reporting_interval=60225 - three bytes that were a temperature and a
/// humidity, reported as a configuration value with no error.
///
/// The check is opt-in on both sides. A caller that does not state the direction gets no
/// check and PS-021 is not satisfied (PS-290); an entry declaring <c>both</c>, or
/// declaring nothing, accepts either direction (PS-287).
/// </summary>
public static class SchemaDirection
{
    public const string Uplink = "uplink";
    public const string Downlink = "downlink";
    public const string Both = "both";

    /// <summary>
    /// Values <c>direction</c> may take on a schema or a port entry (PS-287).
    /// <c>bidirectional</c> appeared in a clause 5 example and is not one of them;
    /// CR-2026-010 withdrew that spelling so a schema carrying it surfaces rather than
    /// being read as <c>both</c>.
    /// </summary>
    static readonly HashSet<string> Declared = new() { Uplink, Downlink, Both };

    /// <summary>
    /// Throws where handling a message of this direction contradicts the entry the
    /// decode or encode would use. Returns quietly where no check applies.
    /// </summary>
    public static void Check(PayloadSchemaDefinition schema, int fPort, string? direction)
    {
        if (string.IsNullOrEmpty(direction))
            return;
        if (direction != Uplink && direction != Downlink)
            throw new ArgumentException(
                $"unknown message direction \"{direction}\"; expected one of downlink, uplink",
                nameof(direction));

        var (declared, label) = SelectEntry(schema, fPort);

        if (declared == null || declared == Both || declared == direction)
            return;
        if (!Declared.Contains(declared))
            throw new InvalidOperationException(
                $"{label} declares unknown direction \"{declared}\"; expected both, downlink, uplink");
        throw new InvalidOperationException(
            $"{label} is declared direction:{declared}; message direction is {direction}");
    }

    /// <summary>
    /// The declared direction of the entry a decode of <paramref name="fPort"/> uses,
    /// with a label naming it. Mirrors the port selection in SchemaDecoder and
    /// SchemaEncoder, so the direction checked and the fields used come from the same
    /// entry (PS-289). The label distinguishes a matched port from the default entry
    /// standing in for one: naming "fPort 42" of a payload the default entry accepted
    /// describes the wrong thing.
    /// </summary>
    static (string? Declared, string Label) SelectEntry(PayloadSchemaDefinition schema, int fPort)
    {
        if (schema.Ports == null)
            return (schema.Direction, $"schema '{schema.Name}'");
        if (schema.Ports.TryGetValue(fPort.ToString(), out var pd))
            return (pd.Direction, $"fPort {fPort}");
        if (schema.Ports.TryGetValue("default", out var dpd))
            return (dpd.Direction, "the default port entry");

        // No entry to check. The caller's own port resolution reports this.
        return (null, $"schema '{schema.Name}'");
    }
}
