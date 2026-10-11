// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-110: <c>on_repeat</c>, how a name reported by more than one tlv entry combines
/// (PS-157, PS-525 to PS-527). Each method mirrors the function of the same purpose in
/// tools/schema_interpreter.py: <c>reported_fields</c>, <c>tlv_repeat_modes</c> and
/// <c>on_repeat_schema_errors</c>. The wording is the spec prototype's.
/// </summary>
public static partial class SchemaParser
{
    static readonly string[] OnRepeatModes = { "collect", "merge", "last" };

    /// <summary>The constructs whose fields report into the enclosing object.</summary>
    static readonly string[] ReportingConstructs = { "match", "byte_group", "flagged", "tlv" };

    /// <summary>
    /// Every named field a field list reports, by name, first declaration first, looking
    /// through the constructs whose fields merge into the enclosing object. An object's
    /// or repeat's members are not descended into.
    /// </summary>
    static List<(string name, YamlMappingNode decl)> ReportedFields(YamlNode? fields)
    {
        var found = new List<(string, YamlMappingNode)>();
        var seen = new HashSet<string>();
        void Walk(YamlNode? items)
        {
            if (items is not YamlSequenceNode seq) return;
            foreach (var item in seq.Children)
            {
                if (item is not YamlMappingNode f) continue;
                if (Has(f, "type"))
                {
                    if (Get(f, "name") is YamlScalarNode n && !string.IsNullOrEmpty(n.Value)
                        && seen.Add(n.Value!))
                        found.Add((n.Value!, f));
                    Walk(Get(f, "trailer"));
                    continue;
                }
                YamlNode? spec = null;
                foreach (var key in ReportingConstructs)
                    if (Get(f, key) is { } s) { spec = s; break; }
                if (spec is YamlSequenceNode)
                    Walk(spec);
                else if (spec is YamlMappingNode sm)
                {
                    Walk(Get(sm, "fields"));
                    if (Get(sm, "cases") is YamlMappingNode cases)
                        foreach (var kv in cases.Children)
                            Walk(kv.Value);
                    if (Get(sm, "groups") is YamlSequenceNode groups)
                        foreach (var g in groups.Children)
                            if (g is YamlMappingNode gm) Walk(Get(gm, "fields"));
                }
            }
        }
        Walk(fields);
        return found;
    }

    /// <summary>A value as the reference's <c>repr</c> writes it in a message.</summary>
    static string ReprOnRepeat(YamlNode node) =>
        YamlString(node) is { } s ? $"'{s}'" : Scalar(node);

    /// <summary>
    /// The mode of each name a tlv reports, the members of each merged object declaring
    /// `last`, and the schema errors (PS-525, PS-526). A field's own `on_repeat` takes
    /// precedence over the tlv's; a tlv's `merge` reaches its `type: object` fields only,
    /// and every other field keeps `collect`. Every declaration of one name must agree.
    /// </summary>
    internal static (Dictionary<string, string> modes, Dictionary<string, HashSet<string>> last,
        List<string> errors) TlvRepeatModes(YamlMappingNode tlv)
    {
        var errors = new List<string>();
        var modes = new Dictionary<string, string>();
        var last = new Dictionary<string, HashSet<string>>();
        var fallback = "collect";
        if (Get(tlv, "on_repeat") is { } d)
        {
            if (YamlString(d) is { } ds && OnRepeatModes.Contains(ds))
                fallback = ds;
            else
                errors.Add($"tlv `on_repeat` is one of collect, merge, last, not {ReprOnRepeat(d)} (PS-525)");
        }
        if (Get(tlv, "cases") is not YamlMappingNode cases) return (modes, last, errors);
        foreach (var kv in cases.Children)
        {
            if (kv.Value is not YamlSequenceNode) continue;
            foreach (var (name, decl) in ReportedFields(kv.Value))
            {
                string? own = null;
                if (Get(decl, "on_repeat") is { } o)
                {
                    own = YamlString(o);
                    if (own == null || !OnRepeatModes.Contains(own))
                    {
                        errors.Add($"Field '{name}': `on_repeat` is one of collect, merge, last, "
                            + $"not {ReprOnRepeat(o)} (PS-525)");
                        continue;
                    }
                }
                bool isObject = YamlString(Get(decl, "type")) == "object";
                if (own == "merge" && !isObject)
                {
                    errors.Add($"Field '{name}': only a `type: object` field may declare "
                        + "`on_repeat: merge` (PS-526)");
                    continue;
                }
                var mode = own ?? (fallback != "merge" || isObject ? fallback : "collect");
                if (modes.TryGetValue(name, out var earlier))
                {
                    if (earlier != mode)
                    {
                        errors.Add($"tlv: the fields reporting '{name}' declare different "
                            + $"`on_repeat`, {earlier} and {mode} (PS-526)");
                        continue;
                    }
                }
                else
                    modes[name] = mode;
                if (mode == "merge")
                {
                    if (!last.TryGetValue(name, out var members))
                        last[name] = members = new HashSet<string>();
                    if (Get(decl, "fields") is YamlSequenceNode memberSeq)
                        foreach (var m in memberSeq.Children)
                            if (m is YamlMappingNode mm && YamlString(Get(mm, "on_repeat")) == "last"
                                && Get(mm, "name") is YamlScalarNode mn && !string.IsNullOrEmpty(mn.Value))
                                members.Add(mn.Value!);
                }
            }
        }
        return (modes, last, errors);
    }

    /// <summary>
    /// PS-526: a schema breaking the on_repeat rules is invalid whether or not a payload
    /// reaches the tlv (on_repeat_schema_errors in the reference). The first error throws.
    /// </summary>
    static void CheckOnRepeat(YamlNode node)
    {
        if (node is YamlMappingNode map)
        {
            if (Get(map, "tlv") is YamlMappingNode tlv)
            {
                var errors = TlvRepeatModes(tlv).errors;
                if (errors.Count > 0) throw new InvalidOperationException(errors[0]);
            }
            foreach (var kv in map.Children)
                if (Scalar(kv.Key) != "test_vectors")
                    CheckOnRepeat(kv.Value);
        }
        else if (node is YamlSequenceNode seq)
        {
            foreach (var item in seq.Children)
                CheckOnRepeat(item);
        }
    }
}
