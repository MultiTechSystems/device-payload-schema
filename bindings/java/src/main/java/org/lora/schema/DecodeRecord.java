package org.lora.schema;

import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * The decode record (PS-572, CR-2026-162), kept while decoding and followed while encoding
 * (PS-573). The reference's {@code DecodeTrace}: a location is the construct's RFC 6901
 * JSON Pointer into the {@code $ref}-expanded schema document, with an occurrence index
 * after each node that repeats at run time - {@code /repeat[i]} after a repeat field's own
 * pointer, {@code tlv[k]} for a tlv entry in payload order, {@code records/n[0]} for a
 * record.
 */
public final class DecodeRecord {
    /** What {@link #lookup} gives for a construct the record says nothing of. */
    static final Object UNRECORDED = new Object();

    private static final Pattern INTEGER_KEY = Pattern.compile("-?\\d+");

    private final Map<Object, String> pointers;
    private final List<Object[]> frames = new ArrayList<>();     // {anchor, kind, index}
    final Map<String, Object> matches = new LinkedHashMap<>();
    final Map<String, Object> internal = new LinkedHashMap<>();
    final Map<String, Object> flagged = new LinkedHashMap<>();

    DecodeRecord(Map<Object, String> pointers, Map<?, ?> record) {
        this.pointers = pointers;
        if (record != null) {
            copy(record.get("matches"), matches);
            copy(record.get("internal"), internal);
            copy(record.get("flagged"), flagged);
        }
    }

    private static void copy(Object table, Map<String, Object> into) {
        if (table instanceof Map<?, ?> map) {
            for (Map.Entry<?, ?> e : map.entrySet()) into.put(String.valueOf(e.getKey()), e.getValue());
        }
    }

    /** One RFC 6901 reference token: {@code ~} as {@code ~0}, {@code /} as {@code ~1}. */
    static String segment(Object key) {
        return String.valueOf(key).replace("~", "~0").replace("/", "~1");
    }

    /**
     * The JSON Pointer of every mapping and list in the expanded document's {@code header},
     * {@code fields} and {@code ports}, by identity (the reference's {@code pointer_map}).
     */
    static Map<Object, String> pointerMap(Map<String, Object> document) {
        Map<Object, String> found = new IdentityHashMap<>();
        for (String key : List.of("header", "fields", "ports")) {
            Object value = document.get(key);
            if (value != null) walk(value, "/" + key, found);
        }
        return found;
    }

    private static void walk(Object node, String pointer, Map<Object, String> found) {
        if (node instanceof Map<?, ?> map) {
            found.putIfAbsent(node, pointer);
            for (Map.Entry<?, ?> e : map.entrySet()) walk(e.getValue(), pointer + "/" + segment(e.getKey()), found);
        } else if (node instanceof List<?> list) {
            found.putIfAbsent(node, pointer);
            for (int i = 0; i < list.size(); i++) walk(list.get(i), pointer + "/" + i, found);
        }
    }

    boolean push(Object node, String kind, int index, String suffix) {
        String anchor = node == null ? null : pointers.get(node);
        if (anchor == null) return false;
        frames.add(new Object[] {anchor + suffix, kind, index});
        return true;
    }

    boolean push(Object node, String kind, int index) {
        return push(node, kind, index, "");
    }

    void pop(boolean pushed) {
        if (pushed) frames.remove(frames.size() - 1);
    }

    String loc(Object node) {
        String pointer = node == null ? null : pointers.get(node);
        if (pointer == null) return null;
        List<Object[]> sorted = new ArrayList<>(frames);
        sorted.sort((a, b) -> Integer.compare(((String) b[0]).length(), ((String) a[0]).length()));
        for (Object[] frame : sorted) {
            String anchor = (String) frame[0];
            if (pointer.equals(anchor) || pointer.startsWith(anchor + "/")) {
                String rest = pointer.substring(anchor.length());
                pointer = "repeat".equals(frame[1])
                        ? anchor + "/repeat[" + frame[2] + "]" + rest
                        : anchor + "[" + frame[2] + "]" + rest;
            }
        }
        return pointer;
    }

    /** The record's entry for a construct at its current location, or {@link #UNRECORDED}. */
    Object lookup(Map<String, Object> table, Object node) {
        String where = loc(node);
        if (where == null || !table.containsKey(where)) return UNRECORDED;
        return table.get(where);
    }

    /** A case key as the record writes it: an integer key as a number, else its text. */
    static Object caseKey(Object key) {
        if (key instanceof Number) return key;
        String text = String.valueOf(key);
        if (INTEGER_KEY.matcher(text).matches()) {
            try {
                return Long.parseLong(text);
            } catch (NumberFormatException ignored) {
                return text;
            }
        }
        return text;
    }

    /** The record as interpreter output: {@code {matches, internal, flagged}}. */
    Map<String, Object> asMap() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("matches", matches);
        out.put("internal", internal);
        out.put("flagged", flagged);
        return out;
    }
}
