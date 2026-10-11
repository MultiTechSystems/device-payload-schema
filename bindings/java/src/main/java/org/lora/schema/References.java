package org.lora.schema;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * PS-539 (CR-2026-117, CR-2026-127): every {@code $} reference names something the schema
 * declares - a field, a {@code var}, a tag component, a repeat's {@code index} or
 * {@code count_as} - and the declaration comes before the reference in schema order
 * (PS-122, PS-147): a tlv's tag components before its cases, a repeat's {@code trailer} and
 * elements before its {@code present_if} and {@code identity}. The document is walked with
 * every {@code $ref} already spliced, so a definition's references are checked where it is
 * used and an unused definition is never checked. A nameless repeat element declares no
 * name (PS-528). {@code metadata} include and timestamp names count as declared first; a
 * {@code metadata.include} {@code source} names runtime input (PS-315) and is not checked.
 * Whether a name declared earlier is bound when it is read is a decode matter (PS-314).
 *
 * <p>The walk is the spec prototype's {@code key_vocabulary.check_references} and
 * {@code reference_errors} in the reference implementation; Go and C# walk the same way,
 * and all four give the message identically.
 */
final class References {
    private References() {}

    /** Keys whose string value may be a {@code $} reference. */
    static final Set<String> REFERENCE_KEYS = Set.of("ref", "a", "b", "field", "count",
            "byte_length", "carry", "instance", "unit", "identity");

    /** Keys holding data, not declarations or references. A match's {@code default} is a field list. */
    static final Set<String> DATA_KEYS = Set.of("lookup", "values", "else", "value");

    private static final Pattern TEMPLATE_REFERENCE = Pattern.compile("\\$\\{(\\w+)\\}");

    private static final Pattern INDEXED_REFERENCE = Pattern.compile("(\\w+)\\[(\\w+)\\]");

    /** PS-539's wording, the spec prototype's. */
    static String message(String name, String key) {
        return "`$" + name + "` (" + key + ") names no field, var, tag component, index or "
                + "count_as the schema declares (PS-539)";
    }

    /** PS-539's ordering rule (CR-2026-127), the spec prototype's wording. */
    static String orderMessage(String name, String key) {
        return "`$" + name + "` (" + key + ") is referenced before the schema declares it (PS-539, PS-122)";
    }

    /** Keys walked before the rest of their mapping: what the rest reads comes after them. */
    static final List<String> FIRST_KEYS = List.of("tag_fields", "trailer", "fields", "element");

    /** Every undeclared or forward reference's message, in schema order, each once. */
    static List<String> errors(Map<String, Object> raw) {
        Set<String> declared = new HashSet<>();
        List<String[]> ahead = new ArrayList<>();
        if (raw.get("metadata") instanceof Map<?, ?> metadata) {
            for (String part : List.of("include", "timestamps")) {
                if (metadata.get(part) instanceof List<?> entries) {
                    for (Object entry : entries) {
                        if (entry instanceof Map<?, ?> m && m.get("name") instanceof String name) {
                            declared.add(name);
                        }
                    }
                }
            }
        }
        walk(raw.get("header"), null, false, declared, ahead);
        walk(raw.get("fields"), null, false, declared, ahead);
        // Each port is a decode of its own (CR-2026-130): what one port declares is never
        // bound while another is decoded, so each starts from what the top level declares.
        Set<String> shared = new HashSet<>(declared);
        Set<String> seen = new HashSet<>(declared);
        if (raw.get("ports") instanceof Map<?, ?> groups) {
            for (Object group : groups.values()) {
                declared.clear();
                declared.addAll(shared);
                walk(group, null, false, declared, ahead);
                seen.addAll(declared);
            }
        }
        declared.clear();
        declared.addAll(seen);   // to tell "never declared" from "declared too late"
        List<String> errors = new ArrayList<>();
        for (String[] reference : ahead) {
            String message = declared.contains(reference[0]) ? orderMessage(reference[0], reference[1])
                    : message(reference[0], reference[1]);
            if (!errors.contains(message)) errors.add(message);
        }
        return errors;
    }

    /** Rejects the schema at the first undeclared reference. */
    static void check(Map<String, Object> raw) {
        List<String> errors = errors(raw);
        if (!errors.isEmpty()) throw new SchemaException(errors.get(0));
    }

    private static void walk(Object node, String key, boolean inSenml, Set<String> declared,
                             List<String[]> ahead) {
        if (node instanceof List<?> list) {
            for (Object item : list) walk(item, null, false, declared, ahead);
            return;
        }
        if (!(node instanceof Map<?, ?> map)) return;
        if (!inSenml) {
            for (String nameKey : List.of("name", "var", "index", "count_as")) {
                if (map.get(nameKey) instanceof String s) declared.add(s);
            }
        }
        List<Object> order = new ArrayList<>();
        for (String first : FIRST_KEYS) if (map.containsKey(first)) order.add(first);
        for (Object k : map.keySet()) if (!FIRST_KEYS.contains(String.valueOf(k))) order.add(k);
        for (Object rawKey : order) {
            String k = String.valueOf(rawKey);
            Object v = map.get(rawKey);
            if (DATA_KEYS.contains(k)) continue;
            if (v instanceof String s) {
                List<String> names = new ArrayList<>();
                if (REFERENCE_KEYS.contains(k) && s.startsWith("$")) {
                    // PS-546: `$name[i]` names a sibling repeat and this one's index.
                    Matcher indexed = INDEXED_REFERENCE.matcher(s.substring(1));
                    if (indexed.matches()) {
                        names.add(indexed.group(1));
                        names.add(indexed.group(2));
                    } else {
                        names.add(s.substring(1));
                    }
                } else if (k.equals("name_from") || (inSenml && k.equals("name"))) {
                    Matcher m = TEMPLATE_REFERENCE.matcher(s);
                    while (m.find()) names.add(m.group(1));
                }
                for (String n : names) if (!declared.contains(n)) ahead.add(new String[] {n, k});
            } else if (k.equals("default")) {
                if ("match".equals(key) && (v instanceof Map || v instanceof List)) {
                    walk(v, null, false, declared, ahead);   // a match's default is a field list
                }
            } else {
                walk(v, k, k.equals("senml"), declared, ahead);
            }
        }
    }
}
