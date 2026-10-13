package org.lora.schema;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/**
 * CR-2026-138 (PS-553): a field has a name; padding is a skip.
 *
 * <p>Mirrors {@code field_name_errors} in tools/schema_interpreter.py, which is the spec
 * prototype's {@code check_field_names}: every field that reads or reports a value has a
 * {@code name}, except a {@code skip}. A construct ({@code match}, {@code byte_group},
 * {@code flagged}, {@code tlv}, {@code records}) is not a field, a byte_group member may
 * leave reserved bits unnamed, and a repeat's {@code element} is nameless (PS-528). Any
 * other nameless field rejects the schema at load, identified by its 1-based position.
 * Before, Java read such a field and dropped it, and the encoder wrote it as zero padding.
 */
final class FieldNames {

    private static final String[] CONSTRUCTS = {"match", "byte_group", "flagged", "tlv"};

    private FieldNames() {}

    /** Throws the first PS-553 violation in the document, after its $refs are spliced. */
    static void check(Map<String, Object> raw) {
        Object declared = raw.get("name");
        String label = declared instanceof String s ? "Schema '" + s + "'" : "Schema None";
        walk(raw.get("header"), label + ", header");
        walk(raw.get("fields"), label);
        if (raw.get("ports") instanceof Map<?, ?> ports) {
            for (Object port : sortedKeys(ports)) {
                Object group = ports.get(port);
                if (group instanceof Map<?, ?> m) group = m.get("fields");
                walk(group, label + ", port " + port);
            }
        }
    }

    /** Integer keys numerically, then the rest as text, so the first error is stable. */
    private static List<Object> sortedKeys(Map<?, ?> map) {
        List<Object> keys = new ArrayList<>(map.keySet());
        keys.sort(Comparator.comparing((Object k) -> asInt(k) == null)
                .thenComparing(k -> asInt(k) == null ? 0L : asInt(k))
                .thenComparing(String::valueOf));
        return keys;
    }

    private static Long asInt(Object key) {
        if (key instanceof Number n) return n.longValue();
        try {
            return Long.parseLong(String.valueOf(key));
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static List<Object> caseBodies(Object cases) {
        List<Object> bodies = new ArrayList<>();
        if (cases instanceof Map<?, ?> m) {
            for (Object key : sortedKeys(m)) bodies.add(m.get(key));
        }
        return bodies;
    }

    private static void walk(Object items, String where) {
        if (!(items instanceof List<?> list)) return;
        int position = 0;
        for (Object entry : list) {
            position++;
            if (!(entry instanceof Map<?, ?> item)) continue;
            boolean hasType = item.containsKey("type");
            if (item.containsKey("records")) {
                if (item.get("records") instanceof List<?> bodies) {
                    for (Object body : bodies) walk(body, where);
                }
                continue;
            }
            boolean construct = false;
            for (String key : CONSTRUCTS) construct |= item.containsKey(key);
            if (construct && !hasType) {
                if (item.get("match") instanceof Map<?, ?> spec) {
                    for (Object body : caseBodies(spec.get("cases"))) walk(body, where);
                    walk(spec.get("default"), where);
                }
                for (String key : new String[] {"tlv", "flagged"}) {
                    Object spec = item.get(key);
                    if (spec instanceof List<?>) {
                        walk(spec, where);
                    } else if (spec instanceof Map<?, ?> m) {
                        walk(m.get("fields"), where);
                        for (Object body : caseBodies(m.get("cases"))) walk(body, where);
                        if (m.get("groups") instanceof List<?> groups) {
                            for (Object g : groups) {
                                if (g instanceof Map<?, ?> gm) walk(gm.get("fields"), where);
                            }
                        }
                    }
                }
                continue;
            }
            Object type = item.get("type");
            if ("skip".equals(type)) continue;
            if (item.containsKey("object") && !hasType) continue;   // PS-466 names it
            if ("match".equals(type)) continue;   // the withdrawn `type: match`, which Cr159 names
            Object nameValue = item.get("name");
            String name = nameValue instanceof String s ? s : "";
            if (name.isEmpty()) {
                String kind = hasType ? String.valueOf(type) : "field";
                throw new SchemaException(where + ": field " + position + ", a " + kind
                        + ", has no name; a field that reads or reports a value is named, "
                        + "and padding is a `skip` (PS-553)");
            }
            String inner = where + ", in '" + name + "'";
            walk(item.get("fields"), inner);
            walk(item.get("trailer"), inner);
        }
    }
}
