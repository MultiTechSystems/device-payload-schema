using System.Linq;
using System.Text.RegularExpressions;
using YamlDotNet.Core;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-189 (PS-621 to PS-623): a repeat of single values whose elements are packed end
/// to end in `packed_bits` bits, most significant bit first, from `bit_offset` bits into the
/// byte at the read position. Mirrors packed_element_width and packed_repeat_error in
/// tools/schema_interpreter.py; the decode is DecodeRepeat's Packed and the encode
/// SchemaEncoder.EncodePacked.
/// </summary>
internal static class Cr189
{
    static readonly Regex Integer = new("^(u|s|uint|int|i)(8|16|24|32|64)$", RegexOptions.CultureInvariant);

    static YamlNode? Get(YamlMappingNode map, string key) =>
        map.Children.TryGetValue(new YamlScalarNode(key), out var v) ? v : null;

    /// <summary>The width in bits of a packed element's integer type and whether it is
    /// signed, or null for any other type.</summary>
    internal static (int width, bool signed)? Width(YamlMappingNode element)
    {
        if (Get(element, "type") is not YamlScalarNode t) return null;
        var m = Integer.Match(t.Value ?? "");
        if (!m.Success) return null;
        return (int.Parse(m.Groups[2].Value), m.Groups[1].Value is not ("u" or "uint"));
    }

    /// <summary>An integer as YAML reads one: a plain scalar, not a bool or a float.</summary>
    static long? PlainInt(YamlNode? node) =>
        node is YamlScalarNode { Style: ScalarStyle.Plain } s && long.TryParse(s.Value, out var n) ? n : null;

    /// <summary>The fields of packed records (PS-639) whose repeat has been checked, so
    /// their own `packed_bits` is read as a part, not as a repeat's (in_record in
    /// packed_repeat_errors). The repeat is checked before its fields are parsed.</summary>
    static readonly System.Runtime.CompilerServices.ConditionalWeakTable<YamlMappingNode, object> RecordMembers = new();

    /// <summary>A packed repeat: `packed_bits` on the repeat (PS-621), or on a field of its
    /// record (PS-639, CR-2026-198). is_packed in tools/schema_interpreter.py.</summary>
    internal static bool IsPacked(YamlMappingNode fm)
    {
        if (Get(fm, "type") is not YamlScalarNode { Value: "repeat" }) return false;
        if (Get(fm, "packed_bits") != null) return true;
        return Get(fm, "fields") is YamlSequenceNode seq
            && seq.Children.Any(c => c is YamlMappingNode m && Get(m, "packed_bits") != null);
    }

    /// <summary>PS-621, PS-639 and PS-640 over one field mapping, in the spec prototype's
    /// text (check_packed_repeats), the first rule broken named.</summary>
    internal static void Check(YamlMappingNode fm)
    {
        if (Get(fm, "type") is not YamlScalarNode typeNode) return;
        bool inRecord = RecordMembers.TryGetValue(fm, out _);
        bool packed = IsPacked(fm);
        if (!((Get(fm, "packed_bits") != null && !inRecord) || Get(fm, "bit_offset") != null || packed)) return;
        var name = Get(fm, "name") is YamlScalarNode n ? n.Value : "None";
        var where = $"Repeat '{name}'";
        if (typeNode.Value != "repeat" || !packed)
        {
            var hint = typeNode.Value == "bool" ? "; did you mean `bit`?" : "";
            throw new InvalidOperationException($"Field '{name}': packed_bits and bit_offset belong to a "
                + $"packed repeat (PS-621, PS-639){hint}");
        }
        if (Get(fm, "packed_bits") != null && Get(fm, "fields") != null)
            throw new InvalidOperationException($"{where}: a packed record declares packed_bits on each field, "
                + "not on the repeat (PS-639)");
        var offsetNode = Get(fm, "bit_offset");
        if (offsetNode != null && !(PlainInt(offsetNode) is >= 0
                || offsetNode is YamlScalarNode { Value: { Length: > 1 } ov } && ov.StartsWith('$')))
            throw new InvalidOperationException($"{where}: bit_offset is a bit count, 0 or more, or a $ reference (PS-621)");
        if (Get(fm, "consume") is { } consume && PlainInt(consume) != 0)
            throw new InvalidOperationException($"{where}: a packed repeat declares consume: 0 or no consume (PS-640)");
        var parts = new List<(YamlNode? bits, YamlNode? element)>();
        if (Get(fm, "packed_bits") is { } pb)
            parts.Add((pb, Get(fm, "element")));
        else
        {
            var members = ((YamlSequenceNode)Get(fm, "fields")!).Children;
            if (!members.All(m => m is YamlMappingNode mm && Get(mm, "packed_bits") != null))
                throw new InvalidOperationException($"{where}: every field of a packed record declares packed_bits (PS-639)");
            foreach (YamlMappingNode m in members)
            {
                parts.Add((Get(m, "packed_bits"), m));
                RecordMembers.AddOrUpdate(m, true);
            }
        }
        foreach (var (bitsNode, element) in parts)
        {
            var bits = PlainInt(bitsNode);
            if (bits is not (>= 1 and <= 32))
                throw new InvalidOperationException($"{where}: packed_bits is an integer from 1 to 32 (PS-621)");
            var width = element is YamlMappingNode em ? Width(em) : null;
            if (width is not { } w || w.width < bits)
                throw new InvalidOperationException($"{where}: a packed part is an integer type at least {bits} "
                    + "bits wide (PS-621, PS-639)");
        }
        if (Get(fm, "count") == null && Get(fm, "byte_length") == null)
            throw new InvalidOperationException($"{where}: a packed repeat declares count or byte_length (PS-621)");
    }

    /// <summary>PS-621 (CR-2026-198): a referenced `bit_offset` that is not a bit count, the
    /// value as JavaScript's String(n) writes it.</summary>
    internal static string OffsetError(string? name, string offset, object? value)
    {
        string shown = value switch
        {
            bool b => b ? "True" : "False",
            string str => $"'{str}'",
            null => "None",
            _ when IsNumber(value) => Wave5.FormatLookupValue(Convert.ToDouble(value)),
            _ => value.ToString() ?? "",
        };
        return $"Repeat '{name}': bit_offset {offset} is {shown}, not a bit count (PS-621)";
    }

    static bool IsNumber(object? v) => v is byte or sbyte or short or ushort or int or uint or long or ulong or float or double or decimal;

    /// <summary>A referenced offset's value, if it is a bit count.</summary>
    internal static int? BitCount(object? value)
    {
        if (value is bool || value is null || !IsNumber(value)) return null;
        var d = Convert.ToDouble(value);
        return d >= 0 && d == Math.Floor(d) && d < int.MaxValue ? (int)d : null;
    }
}
