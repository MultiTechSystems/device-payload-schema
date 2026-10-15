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
        var shared = new Dictionary<(string obj, string member), List<YamlMappingNode>>();
        var objects = new Dictionary<string, HashSet<string>>();   // paths combined as objects
        var sharedOrder = new List<(string obj, string member)>();
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
                    // CR-2026-173: a member that is an object is combined member by member
                    // too. PS-526 concerns two cases declaring a leaf; one case naming a
                    // member twice counts once.
                    if (!last.TryGetValue(name, out var members))
                        last[name] = members = new HashSet<string>();
                    var inCase = new HashSet<string>();
                    foreach (var (path, mm, memberIsObject) in MergedMembers(Get(decl, "fields"), new List<string>()))
                    {
                        var pk = PathKey(path);
                        if (memberIsObject)
                        {
                            if (!objects.TryGetValue(name, out var os)) objects[name] = os = new HashSet<string>();
                            os.Add(pk);
                            continue;
                        }
                        if (!inCase.Add(pk)) continue;
                        var key = (name, pk);
                        if (!shared.TryGetValue(key, out var decls))
                        {
                            shared[key] = decls = new List<YamlMappingNode>();
                            sharedOrder.Add(key);
                        }
                        decls.Add(mm);
                        if (YamlString(Get(mm, "on_repeat")) == "last") members.Add(pk);
                    }
                }
            }
        }
        foreach (var key in sharedOrder)
        {
            var decls = shared[key];
            var dotted = Dotted(key.member);
            if (objects.TryGetValue(key.obj, out var os) && os.Contains(key.member))
            {
                errors.Add($"tlv: the merged object '{key.obj}' declares '{dotted}' as an object "
                    + "in one case and as a value in another (PS-526)");
                continue;
            }
            // CR-2026-128: merged cases share a leaf only where it declares `last`, at any
            // depth (CR-2026-173).
            if (decls.Count > 1 && decls.Any(d => YamlString(Get(d, "on_repeat")) != "last"))
                errors.Add($"tlv: cases share the member '{dotted}' of the merged object "
                    + $"'{key.obj}' without `on_repeat: last` (PS-526)");
        }
        return (modes, last, errors);
    }

    /// <summary>
    /// merged_members (CR-2026-173): each named member of a merged object with its path,
    /// where a member of <c>type: object</c> not declaring <c>on_repeat: last</c> is
    /// combined member by member and anything else is a leaf.
    /// </summary>
    static List<(List<string> path, YamlMappingNode decl, bool isObject)> MergedMembers(
        YamlNode? fields, List<string> path)
    {
        var found = new List<(List<string>, YamlMappingNode, bool)>();
        if (fields is not YamlSequenceNode seq) return found;
        foreach (var m in seq.Children)
        {
            if (m is not YamlMappingNode mm || Get(mm, "name") is not YamlScalarNode mn
                || string.IsNullOrEmpty(mn.Value)) continue;
            var here = new List<string>(path) { mn.Value! };
            bool isObject = YamlString(Get(mm, "type")) == "object"
                && YamlString(Get(mm, "on_repeat")) != "last";
            found.Add((here, mm, isObject));
            if (isObject) found.AddRange(MergedMembers(Get(mm, "fields"), here));
        }
        return found;
    }

    /// <summary>A member path as one key; a name may hold a dot.</summary>
    internal static string PathKey(IEnumerable<string> path) => string.Join("\u0000", path);

    /// <summary>A path key as <c>a.b</c>, as a message writes it.</summary>
    internal static string Dotted(string key) => key.Replace('\u0000', '.');

    /// <summary>
    /// A merged object field's leaf paths and the paths it combines as objects, read from
    /// its typed members (CR-2026-173).
    /// </summary>
    internal static void TypedMembers(List<SchemaField> fields, List<string> path,
        HashSet<string>? leaves, HashSet<string>? objects)
    {
        foreach (var m in fields)
        {
            if (string.IsNullOrEmpty(m.Name)) continue;
            var here = new List<string>(path) { m.Name };
            bool last = m.Node != null && YamlString(Get(m.Node, "on_repeat")) == "last";
            if (m.Type == FieldType.Object && !last)
            {
                objects?.Add(PathKey(here));
                TypedMembers(m.Fields, here, leaves, objects);
            }
            else
                leaves?.Add(PathKey(here));
        }
    }

    internal static HashSet<string> MergedLeaves(SchemaField f)
    {
        var leaves = new HashSet<string>();
        TypedMembers(f.Fields, new List<string>(), leaves, null);
        return leaves;
    }

    /// <summary>leaf_paths: a value's leaf paths, descending only into the declared objects.</summary>
    internal static void LeafPaths(Dictionary<string, object?> value, HashSet<string> objects,
        List<string> path, HashSet<string> output)
    {
        foreach (var (k, v) in value)
        {
            var here = new List<string>(path) { k };
            if (v is Dictionary<string, object?> sub && objects.Contains(PathKey(here)))
                LeafPaths(sub, objects, here, output);
            else
                output.Add(PathKey(here));
        }
    }

    /// <summary>value_at: the part of a nested value holding exactly the given leaf paths.</summary>
    internal static Dictionary<string, object?> ValueAt(Dictionary<string, object?> value, IEnumerable<string> keys)
    {
        var output = new Dictionary<string, object?>();
        foreach (var key in keys)
        {
            var path = key.Split('\u0000');
            var src = value;
            var dst = output;
            for (int i = 0; i < path.Length - 1; i++)
            {
                src = (Dictionary<string, object?>)src[path[i]]!;
                if (!dst.TryGetValue(path[i], out var next) || next is not Dictionary<string, object?> nd)
                    dst[path[i]] = nd = new Dictionary<string, object?>();
                dst = nd;
            }
            dst[path[^1]] = src[path[^1]];
        }
        return output;
    }

    /// <summary>Mappings whose keys are data, and `definitions`, checked where spliced.</summary>
    static readonly string[] OnRepeatOpaque = { "lookup", "values", "test_vectors", "metadata", "definitions" };

    /// <summary>
    /// PS-526: a schema breaking the on_repeat rules is invalid whether or not a payload
    /// reaches the tlv, and PS-525 (CR-2026-125): an `on_repeat` where it has no effect -
    /// anywhere but a tlv, a field a tlv case reports, or `last` on a member of a merged
    /// object - makes the schema invalid (on_repeat_schema_errors in the reference). The
    /// first error throws. Declarations are compared by identity.
    /// </summary>
    static void CheckOnRepeat(YamlNode root)
    {
        var allowed = new HashSet<YamlNode>(ReferenceEqualityComparer.Instance);
        void Tlvs(YamlNode node)
        {
            if (node is YamlMappingNode map)
            {
                if (Get(map, "tlv") is YamlMappingNode tlv)
                {
                    allowed.Add(tlv);
                    var (modes, _, errors) = TlvRepeatModes(tlv);
                    if (errors.Count > 0) throw new InvalidOperationException(errors[0]);
                    if (Get(tlv, "cases") is YamlMappingNode cases)
                        foreach (var kv in cases.Children)
                        {
                            if (kv.Value is not YamlSequenceNode) continue;
                            foreach (var (name, decl) in ReportedFields(kv.Value))
                            {
                                allowed.Add(decl);
                                if (!modes.TryGetValue(name, out var mode) || mode != "merge") continue;
                                // A merged object's leaf may declare `last` at any depth
                                // (PS-525, CR-2026-173).
                                foreach (var (_, mm, _) in MergedMembers(Get(decl, "fields"), new List<string>()))
                                    if (YamlString(Get(mm, "on_repeat")) == "last")
                                        allowed.Add(mm);
                            }
                        }
                }
                foreach (var kv in map.Children)
                    if (!OnRepeatOpaque.Contains(Scalar(kv.Key)))
                        Tlvs(kv.Value);
            }
            else if (node is YamlSequenceNode seq)
                foreach (var item in seq.Children)
                    Tlvs(item);
        }
        void Strays(YamlNode node)
        {
            if (node is YamlMappingNode map)
            {
                if (Has(map, "on_repeat") && !allowed.Contains(map))
                {
                    var name = Get(map, "name") is YamlScalarNode n ? n.Value : "an entry";
                    throw new InvalidOperationException($"`on_repeat` on '{name}' has no effect there: "
                        + "it belongs on a tlv, a field of a tlv case, or a merged object's member (PS-525)");
                }
                foreach (var kv in map.Children)
                    if (!OnRepeatOpaque.Contains(Scalar(kv.Key)))
                        Strays(kv.Value);
            }
            else if (node is YamlSequenceNode seq)
                foreach (var item in seq.Children)
                    Strays(item);
        }
        Tlvs(root);
        Strays(root);
    }
}
