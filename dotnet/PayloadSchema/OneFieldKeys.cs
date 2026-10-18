// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-194 (PS-436, PS-437, PS-438, PS-440, PS-270): a one-field tlv tag may use the keys
/// <c>"[*]"</c> (any value) and <c>"[!n]"</c> (any value but n), ranked below every exact key;
/// a tlv has no <c>default</c> case. <c>FindTLVCaseKey</c> already ranked the keys (by
/// <c>MatchCompositeCaseKey</c>'s specificity); this is the load check, the spec prototype's
/// <c>check_one_field_keys</c>, in its words.
/// </summary>
internal static class OneFieldKeys
{
    internal const string DefaultError =
        "A tlv has no `default` case; its catch-all is the key \"[*]\" (PS-436, PS-438)";

    /// <summary>The prototype's one_element_key: "*" or "!n" and its PS-270 rank, else null.</summary>
    internal static (string Element, int Rank)? Parse(string key)
    {
        var text = key.Trim();
        if (!text.StartsWith('[') || !text.EndsWith(']') || text.Contains(',')) return null;
        var inner = text[1..^1].Trim();
        if (inner == "*") return ("*", 2);
        if (!inner.StartsWith('!')) return null;
        var n = inner[1..].Trim();
        bool ok;
        long value;
        if (n.StartsWith("0x", StringComparison.OrdinalIgnoreCase))
            ok = long.TryParse(n[2..], NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out value);
        else
            ok = long.TryParse(n, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out value);
        return ok ? ("!" + value.ToString(CultureInfo.InvariantCulture), 1) : null;
    }

    /// <summary>The first problem with a tlv's case keys, in declaration order, else null.</summary>
    internal static string? Error(YamlMappingNode tlv, YamlMappingNode cases)
    {
        var keys = cases.Children.Keys.Select(k => k is YamlScalarNode s ? s.Value ?? "" : "").ToList();
        if (keys.Contains("default")) return DefaultError;
        if (tlv.Children.TryGetValue(new YamlScalarNode("tag_key"), out var tagKey)
            && tagKey is YamlSequenceNode seq && seq.Children.Count > 1
            && tlv.Children.ContainsKey(new YamlScalarNode("tag_fields")))
            return null;    // a composite tag's keys are PS-436's [e1, e2, ...] form
        var parsed = keys.Select(k => (Key: k, Parsed: Parse(k)))
            .Where(p => p.Parsed != null).ToList();
        for (int i = 0; i < parsed.Count; i++)
            for (int j = i + 1; j < parsed.Count; j++)
            {
                var (a, pa) = parsed[i];
                var (b, pb) = parsed[j];
                if (pa!.Value.Element == pb!.Value.Element)
                    return $"TLV keys '{a}' and '{b}' are the same key (PS-437)";
                if (pa.Value.Rank == pb.Value.Rank)
                    return $"TLV keys '{a}' and '{b}' have the same rank and can match the same tag (PS-440)";
            }
        return null;
    }
}
