using System.Globalization;
using System.Numerics;
using System.Text.RegularExpressions;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-187 (PS-616 to PS-618): <c>bit_labels</c> on an unsigned integer or unsigned bit
/// range reports the labels of its set bits, as a list or, with <c>join</c>, as one text.
/// Mirrors bit_labels_error, decode_bit_labels and encode_bit_labels in
/// tools/schema_interpreter.py, in the spec prototype's text.
/// </summary>
internal static class BitLabels
{
    static readonly string[] Keys = { "bit_labels", "join", "none", "order" };
    /// <summary>The keys PS-616 rejects beside bit_labels, in the prototype's order.</summary>
    static readonly string[] Excluded =
        { "lookup", "add", "mult", "div", "transform", "polynomial", "time", "compute", "value", "encoding" };
    static readonly Dictionary<string, int> Whole = new()
    {
        ["u8"] = 8, ["u16"] = 16, ["u24"] = 24, ["u32"] = 32, ["u64"] = 64,
        ["uint8"] = 8, ["uint16"] = 16, ["uint24"] = 24, ["uint32"] = 32, ["uint64"] = 64,
    };
    static readonly Regex Range = new(@"^u(\d+)\[(\d+):(\d+)\]$", RegexOptions.CultureInvariant);

    internal record Entry(object? Key, int? Bit, object? Label);

    static int? Width(object? type)
    {
        if (type is not string t) return null;
        t = t.Trim();
        if (Whole.TryGetValue(t, out var w)) return w;
        var m = Range.Match(t);
        if (m.Success)
        {
            int lo = int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture);
            int hi = int.Parse(m.Groups[3].Value, CultureInfo.InvariantCulture);
            if (hi >= lo) return hi - lo + 1;
        }
        return null;
    }

    static List<Entry>? Entries(object? raw)
    {
        if (raw is not Dictionary<string, object?> map) return null;
        var list = new List<Entry>();
        foreach (var kv in map)
        {
            object? key = kv.Key;
            int? bit = null;
            if (kv.Key.Length > 0 && kv.Key.All(char.IsAsciiDigit)
                && int.TryParse(kv.Key, NumberStyles.None, CultureInfo.InvariantCulture, out var n))
            {
                bit = n;
                key = (long)n;
            }
            else if (kv.Key.StartsWith('-') && long.TryParse(kv.Key, NumberStyles.AllowLeadingSign,
                         CultureInfo.InvariantCulture, out var neg))
            {
                key = neg;           // a negative bit, outside any width
            }
            list.Add(new Entry(key, bit, kv.Value));
        }
        return list;
    }

    static Dictionary<string, object?>? Declaration(SchemaField field) =>
        field.Node == null ? null : Meta.FromYaml(field.Node) as Dictionary<string, object?>;

    internal static bool Declared(SchemaField? field) =>
        field?.Node != null && field.Node.Children.TryGetValue(new YamlScalarNode("bit_labels"), out var v)
        && Meta.FromYaml(v) != null;

    /// <summary>PS-616, PS-617 at load over one field mapping: the first error, in the prototype's order.</summary>
    internal static void Check(YamlMappingNode node)
    {
        if (Meta.FromYaml(node) is not Dictionary<string, object?> fm || !fm.ContainsKey("type")) return;
        if (!Keys.Any(fm.ContainsKey)) return;
        var where = $"Field '{(fm.GetValueOrDefault("name") is { } nm ? Convert.ToString(nm, CultureInfo.InvariantCulture) : "None")}'";
        var labels = fm.GetValueOrDefault("bit_labels");
        if (labels == null)
            throw new InvalidOperationException($"{where}: join, none and order belong to bit_labels (PS-616)");
        var width = Width(fm["type"]);
        if (width == null)
            throw new InvalidOperationException($"{where}: bit_labels is declared on an unsigned integer or an "
                + $"unsigned bit range, not {Meta.PyRepr(fm["type"])} (PS-616)");
        var clash = Excluded.Where(fm.ContainsKey).ToList();
        if (clash.Count > 0)
            throw new InvalidOperationException(
                $"{where}: bit_labels reports labels, so {string.Join(", ", clash)} does not apply (PS-616)");
        var entries = Entries(labels);
        if (entries == null || entries.Count == 0)
            throw new InvalidOperationException($"{where}: bit_labels maps bit numbers to labels (PS-616)");
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var e in entries)
        {
            if (e.Bit is not int bit || bit < 0 || bit >= width)
                throw new InvalidOperationException(
                    $"{where}: bit {Meta.PyRepr(e.Key)} is outside its {width} bits (PS-617)");
            if (e.Label is not string label)
                throw new InvalidOperationException($"{where}: the label of bit {bit} is a string (PS-616)");
            if (!seen.Add(label))
                throw new InvalidOperationException($"{where}: two bits have the label {Meta.PyRepr(label)} (PS-617)");
        }
        if (fm.TryGetValue("join", out var join) && join != null)
        {
            if (join is not string j)
                throw new InvalidOperationException($"{where}: join is a string (PS-616)");
            if (j.Length > 0 && seen.Any(l => l.Contains(j, StringComparison.Ordinal)))
                throw new InvalidOperationException(
                    $"{where}: a label contains the join text {Meta.PyRepr(j)} (PS-617)");
        }
        if (fm.TryGetValue("none", out var none) && none != null && (none is not string ns || seen.Contains(ns)))
            throw new InvalidOperationException($"{where}: none is a string that no bit has as its label (PS-616)");
        var order = fm.TryGetValue("order", out var o) ? o : "ascending";
        if (order is not ("ascending" or "descending"))
            throw new InvalidOperationException($"{where}: order is ascending or descending (PS-616)");
    }

    /// <summary>(bit, label) in the field's declared order (PS-616).</summary>
    static List<Entry> Table(Dictionary<string, object?> decl)
    {
        var table = (Entries(decl["bit_labels"]) ?? new List<Entry>())
            .OrderBy(e => e.Bit ?? int.MaxValue).ToList();
        if (decl.GetValueOrDefault("order") is "descending") table.Reverse();
        return table;
    }

    /// <summary>A decoded value as the unsigned number its bits are read from, else null.</summary>
    internal static BigInteger? Number(object? value) => value switch
    {
        ulong u => u,
        long l when l >= 0 => l,
        int i when i >= 0 => i,
        uint ui => ui,
        double d when d >= 0 && d == Math.Floor(d) && !double.IsInfinity(d) => new BigInteger(d),
        _ => null,
    };

    /// <summary>PS-616, PS-617: the labels of the set bits the mapping lists; an unlisted set bit warns.</summary>
    internal static object Decode(SchemaField field, BigInteger n, DecodeContext ctx)
    {
        var decl = Declaration(field)!;
        var table = Table(decl);
        var listed = new HashSet<int>(table.Where(e => e.Bit != null).Select(e => e.Bit!.Value));
        for (int bit = 0; (BigInteger.One << bit) <= n; bit++)
        {
            if (!((n >> bit) & 1).IsZero && !listed.Contains(bit))
                ctx.Warnings.Add($"Field '{field.Name}': bit {bit} is set and has no label; it is not reported (PS-617)");
        }
        var labels = new List<object?>();
        foreach (var e in table)
            if (e.Bit is int b && b >= 0 && !((n >> b) & 1).IsZero)
                labels.Add(e.Label);
        if (labels.Count == 0 && decl.GetValueOrDefault("none") is string none)
            labels.Add(none);
        if (decl.ContainsKey("join"))
            return string.Join(decl["join"] as string ?? "", labels.Select(l => Convert.ToString(l, CultureInfo.InvariantCulture)));
        return labels;
    }

    /// <summary>
    /// PS-618: the number with the bits of the given labels set, every other bit 0. A list, or
    /// the joined text where <c>join</c> is declared; an empty one or the <c>none</c> label is 0.
    /// </summary>
    internal static long Encode(SchemaField field, object? value, Func<object?, string> messageValue)
    {
        var name = field.Name;
        var decl = Declaration(field)!;
        var byLabel = new Dictionary<string, int>(StringComparer.Ordinal);
        var labels = new List<string>();
        foreach (var e in Table(decl))
            if (e.Label is string s && e.Bit is int b)
            {
                byLabel[s] = b;
                labels.Add(s);
            }
        bool hasJoin = decl.ContainsKey("join");
        var none = decl.GetValueOrDefault("none") as string;
        var items = new List<object?>();
        if (value is string text && hasJoin)
        {
            var join = decl["join"] as string ?? "";
            if (text.Length == 0 || text == none)
            {
                // nothing set
            }
            else if (join.Length > 0)
                items.AddRange(text.Split(join));
            else
            {
                labels = labels.OrderByDescending(l => l.Length).ToList();
                if (labels.Any(a => labels.Any(b => a != b && b.StartsWith(a, StringComparison.Ordinal))))
                    throw new InvalidOperationException($"Field '{name}': with an empty join, one label is a "
                        + "prefix of another, so its text cannot be split; the schema decodes but is not "
                        + "encoded (PS-617)");
                var rest = text;
                while (rest.Length > 0)
                {
                    var found = labels.FirstOrDefault(l => rest.StartsWith(l, StringComparison.Ordinal));
                    if (found == null)
                        throw new InvalidOperationException(
                            $"Field '{name}': {Meta.PyRepr(rest)} does not begin with a label (PS-618)");
                    items.Add(found);
                    rest = rest[found.Length..];
                }
            }
        }
        else if (value is System.Collections.IList list && value is not string)
        {
            foreach (var item in list)
                if (!(none != null && item is string s && s == none))
                    items.Add(item);
        }
        else
            throw new InvalidOperationException($"Field '{name}': {messageValue(value)} is not a list of labels"
                + (hasJoin ? " or joined text" : "") + " (PS-618)");
        long number = 0;
        foreach (var item in items)
        {
            if (item is not string s || !byLabel.TryGetValue(s, out var bit))
                throw new InvalidOperationException($"Field '{name}': {messageValue(item)} is not one of its labels (PS-618)");
            number |= 1L << bit;
        }
        return number;
    }
}
