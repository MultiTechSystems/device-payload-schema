namespace PayloadSchema;

/// <summary>
/// CR-2026-193 (PS-630, PS-631): a tlv entry with a length bounds its case. A case field
/// reading past the entry's value bytes is an error naming the entry's tag and frame offset;
/// value bytes the case leaves unread are skipped with a warning, and the next entry begins
/// after them. The texts are the spec prototype's.
/// </summary>
internal static class TlvEntryBound
{
    /// <summary>The entry as the prototype names it: a one-part tag a decimal, a composite a list.</summary>
    internal static string Where(List<int> tag, int start)
    {
        var text = tag.Count == 1
            ? tag[0].ToString(System.Globalization.CultureInfo.InvariantCulture)
            : "[" + string.Join(", ", tag.Select(p => p.ToString(System.Globalization.CultureInfo.InvariantCulture))) + "]";
        return $"tlv entry tag {text} at offset {start}";
    }

    /// <summary>PS-630: a short read inside an entry.</summary>
    internal static InvalidOperationException Overrun(string where, Exception under, int length) =>
        new($"{where}: {under.Message}; the case reads past the entry's {length} value byte(s) (PS-630)");

    /// <summary>PS-631: value bytes the case left unread.</summary>
    internal static string Unread(string where, int count) =>
        $"{where}: {count} byte(s) of its value not read (PS-631)";
}
