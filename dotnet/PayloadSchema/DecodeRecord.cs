// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Runtime.CompilerServices;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// The decode record (PS-572, CR-2026-162), kept while decoding and followed while encoding
/// (PS-573). Mirrors DecodeTrace in tools/schema_interpreter.py, itself the spec
/// prototype's: a location is the construct's RFC 6901 JSON Pointer into the
/// `$ref`-expanded document, with an occurrence index after each node that repeats at run
/// time - <c>/repeat[i]</c> after a repeat field's own pointer, <c>tlv[k]</c> for a tlv
/// entry in payload order, and <c>records/n[0]</c> for a record.
/// </summary>
internal sealed class DecodeTrace
{
    readonly Dictionary<YamlNode, string> _pointers;
    readonly List<(string anchor, string kind, int index)> _frames = new();

    public Dictionary<string, object?> Matches { get; } = new();
    public Dictionary<string, object?> Internal { get; } = new();
    public Dictionary<string, object?> Flagged { get; } = new();

    /// <summary>Names whose value the record, not the input, supplied (per encode).</summary>
    public HashSet<string> Supplied { get; } = new();

    /// <summary>The names the encode's input gives.</summary>
    public HashSet<string> InputNames { get; } = new();

    public DecodeTrace(PayloadSchemaDefinition schema, Dictionary<string, object?>? record = null)
    {
        _pointers = Pointers(schema);
        if (record == null) return;
        Copy(record, "matches", Matches);
        Copy(record, "internal", Internal);
        Copy(record, "flagged", Flagged);
    }

    static void Copy(Dictionary<string, object?> record, string key, Dictionary<string, object?> into)
    {
        if (record.TryGetValue(key, out var table) && table is IDictionary<string, object?> map)
            foreach (var kv in map) into[kv.Key] = kv.Value;
    }

    static readonly ConditionalWeakTable<PayloadSchemaDefinition, Dictionary<YamlNode, string>> Cache = new();

    /// <summary>
    /// PS-572: the JSON Pointer of every mapping and list in the expanded document, by
    /// identity (pointer_map). A key is written as Python's str() of the resolved YAML key,
    /// so <c>0x01:</c> is <c>1</c>, as PyYAML loads it.
    /// </summary>
    static Dictionary<YamlNode, string> Pointers(PayloadSchemaDefinition schema)
        => Cache.GetValue(schema, s =>
        {
            var found = new Dictionary<YamlNode, string>(ReferenceEqualityComparer.Instance);
            void Walk(YamlNode node, string pointer)
            {
                if (node is YamlMappingNode map)
                {
                    found.TryAdd(node, pointer);
                    foreach (var kv in map.Children)
                        Walk(kv.Value, pointer + "/" + Segment(Meta.KeyText(kv.Key)));
                }
                else if (node is YamlSequenceNode seq)
                {
                    found.TryAdd(node, pointer);
                    for (int i = 0; i < seq.Children.Count; i++)
                        Walk(seq.Children[i], pointer + "/" + i);
                }
            }
            if (s.Root != null)
                foreach (var key in new[] { "header", "fields", "ports" })
                    foreach (var kv in s.Root.Children)
                        if (kv.Key is YamlScalarNode k && k.Value == key
                            && kv.Value is YamlMappingNode or YamlSequenceNode)
                            Walk(kv.Value, "/" + key);
            return found;
        });

    /// <summary>One RFC 6901 reference token: `~` as `~0`, `/` as `~1`.</summary>
    internal static string Segment(string key) => key.Replace("~", "~0").Replace("/", "~1");

    public bool Push(YamlNode? node, string kind, int index, string suffix = "")
    {
        if (node == null || !_pointers.TryGetValue(node, out var anchor)) return false;
        _frames.Add((anchor + suffix, kind, index));
        return true;
    }

    public void Pop(bool pushed)
    {
        if (pushed) _frames.RemoveAt(_frames.Count - 1);
    }

    public string? Loc(YamlNode? node)
    {
        if (node == null || !_pointers.TryGetValue(node, out var pointer)) return null;
        foreach (var (anchor, kind, index) in _frames.OrderByDescending(f => f.anchor.Length))
        {
            if (pointer == anchor || pointer.StartsWith(anchor + "/", StringComparison.Ordinal))
            {
                var rest = pointer[anchor.Length..];
                pointer = kind == "repeat" ? $"{anchor}/repeat[{index}]{rest}" : $"{anchor}[{index}]{rest}";
            }
        }
        return pointer;
    }

    /// <summary>The record's entry for a node at its current location: found, and the value.</summary>
    public (bool found, object? value) Lookup(Dictionary<string, object?> table, YamlNode? node)
    {
        var where = Loc(node);
        if (where == null || !table.TryGetValue(where, out var value)) return (false, null);
        return (true, value);
    }

    public Dictionary<string, object?> AsDict() => new()
    {
        ["matches"] = Matches,
        ["internal"] = Internal,
        ["flagged"] = Flagged,
    };

    /// <summary>
    /// A value as the reference's normalize_output reports it: an integral double as an
    /// integer, bytes as hex; NaN and infinity have no JSON form and are not recorded.
    /// </summary>
    public static (bool ok, object? value) Normalize(object? value)
    {
        switch (value)
        {
            case null: return (false, null);
            case bool b: return (true, b);
            case double d:
                if (double.IsNaN(d) || double.IsInfinity(d)) return (false, null);
                if (d == Math.Floor(d) && Math.Abs(d) < 9.2e18) return (true, (long)d);
                return (true, d);
            case float f: return Normalize((double)f);
            case int i: return (true, (long)i);
            case uint u: return (true, (long)u);
            case ulong ul: return (true, ul <= long.MaxValue ? (object)(long)ul : (double)ul);
            case byte[] bytes: return (true, Convert.ToHexString(bytes).ToLowerInvariant());
            default: return (true, value);
        }
    }

    /// <summary>
    /// A case key as the record writes it: the YAML key as PyYAML resolves it - an integer
    /// key as an integer, anything else as its text.
    /// </summary>
    public static object? CaseKey(YamlNode? key)
    {
        if (key == null) return "default";
        var resolved = Meta.FromYaml(key);
        return resolved is long or bool ? resolved : Meta.KeyText(key);
    }

    /// <summary>
    /// Every data field a field list can report, by name, looking through the constructs
    /// whose fields merge into the enclosing object (record_declared_fields): what PS-573's
    /// "a field only another case declares" is checked against. A match's `default` list
    /// beside its cases is looked into too, where the spec prototype's is not.
    /// </summary>
    public static List<string> DeclaredFields(IEnumerable<YamlNode>? items, YamlNode? skip = null)
    {
        var found = new List<string>();
        void Walk(IEnumerable<YamlNode>? list)
        {
            if (list == null) return;
            foreach (var item in list)
            {
                if (item is not YamlMappingNode map || ReferenceEquals(item, skip)) continue;
                if (Get(map, "type") != null)
                {
                    if (Get(map, "name") is YamlScalarNode { Value: { Length: > 0 } name } && !found.Contains(name))
                        found.Add(name);
                    Walk((Get(map, "trailer") as YamlSequenceNode)?.Children);
                    continue;
                }
                YamlNode? spec = null;
                foreach (var key in new[] { "match", "tlv", "flagged", "byte_group", "records" })
                    if ((spec = Get(map, key)) != null) break;
                if (spec is YamlSequenceNode seq)
                {
                    foreach (var part in seq.Children)
                        Walk(part is YamlSequenceNode inner ? inner.Children : new[] { part });
                }
                else if (spec is YamlMappingNode specMap)
                {
                    Walk((Get(specMap, "fields") as YamlSequenceNode)?.Children);
                    if (Get(specMap, "cases") is YamlMappingNode cases)
                        foreach (var kv in cases.Children)
                            Walk((kv.Value as YamlSequenceNode)?.Children);
                    if (Get(specMap, "default") is YamlSequenceNode dflt)
                        Walk(dflt.Children);
                    if (Get(specMap, "groups") is YamlSequenceNode groups)
                        foreach (var g in groups.Children)
                            if (g is YamlMappingNode gm)
                                Walk((Get(gm, "fields") as YamlSequenceNode)?.Children);
                }
            }
        }
        Walk(items);
        return found;
    }

    static YamlNode? Get(YamlMappingNode map, string key)
    {
        foreach (var kv in map.Children)
            if (kv.Key is YamlScalarNode s && s.Value == key)
                return kv.Value;
        return null;
    }
}
