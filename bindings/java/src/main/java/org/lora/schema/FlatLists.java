package org.lora.schema;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * CR-2026-195 (PS-525 amended, PS-632 to PS-634): flat lists. {@code on_repeat: concat} on
 * a tlv reports a name as one array however many entries report it, splicing a list value
 * (PS-632); {@code flatten} on a repeat with {@code fields} reports its element's inner
 * repeat's arrays as one list (PS-633). Both decode; a concat whose case reports a list,
 * and any flatten, are refused for encoding (PS-634). The messages are the spec
 * prototype's.
 */
final class FlatLists {
    private FlatLists() {}

    /** One more report of a concat name: appended, its elements spliced where it is a list. */
    @SuppressWarnings("unchecked")
    static List<Object> concat(Object existing, Object value) {
        List<Object> out = existing instanceof List<?> l ? (List<Object>) l : new ArrayList<>();
        if (value instanceof List<?> list) out.addAll(list);
        else out.add(value);
        return out;
    }

    /** PS-634 for a concat name whose case reports a list. */
    static SchemaException.EncodeException concatListError(String name) {
        return new SchemaException.EncodeException("tlv: '" + name + "' is concatenated from lists, "
                + "which cannot be split back into entries; the schema decodes but is not encoded (PS-634)");
    }

    /** PS-634 for a repeat declaring flatten. */
    static SchemaException.EncodeException flattenError(String name) {
        return new SchemaException.EncodeException("Repeat '" + name + "' flattens its groups into "
                + "one list, which cannot be split back into groups; the schema decodes but is not "
                + "encoded (PS-634)");
    }

    /** The prototype's check_flatten for one field mapping (PS-633). */
    static void check(Map<String, Object> fm) {
        if (!fm.containsKey("flatten") || !fm.containsKey("type")) return;
        String where = "Repeat '" + (fm.get("name") == null ? "None" : fm.get("name")) + "'";
        Object target = fm.get("flatten");
        List<?> fields = "repeat".equals(fm.get("type")) && fm.get("fields") instanceof List<?> l ? l : null;
        Map<?, ?> inner = null;
        if (fields != null) {
            for (Object item : fields) {
                if (item instanceof Map<?, ?> f && target != null && target.equals(f.get("name"))) {
                    inner = f;
                    break;
                }
            }
        }
        if (inner == null || !"repeat".equals(inner.get("type"))) {
            throw new SchemaException(where + ": flatten names a repeat among its element's fields (PS-633)");
        }
        List<String> others = new ArrayList<>();
        for (Object item : fields) {
            if (!(item instanceof Map<?, ?> f) || !f.containsKey("type")) continue;
            Object name = f.get("name");
            if (name == null || target.equals(name) || String.valueOf(name).startsWith("_")) continue;
            others.add("'" + name + "'");
        }
        if (!others.isEmpty()) {
            throw new SchemaException(where + ": with flatten, its element's other fields are internal; "
                    + String.join(", ", others) + " would be reported (PS-633)");
        }
    }
}
