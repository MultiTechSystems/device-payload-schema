package org.lora.schema;

import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * CR-2026-110: how a name reported by more than one entry of a tlv combines (PS-157,
 * PS-525 to PS-527). Mirrors {@code reported_fields}, {@code tlv_repeat_modes} and
 * {@code combine_repeated} in tools/schema_interpreter.py, with its messages.
 *
 * <p>A field's own {@code on_repeat} takes precedence over the tlv's. A tlv's
 * {@code merge} reaches its {@code type: object} fields only; any other field keeps
 * {@code collect}. With no {@code on_repeat} anywhere every field collects, as PS-157
 * always has. Every declaration of one name must agree (PS-526).
 */
final class OnRepeat {
    static final List<String> MODES = List.of("collect", "merge", "last");
    private static final List<String> REPORTING = List.of("match", "byte_group", "flagged", "tlv");

    /**
     * The resolved mode by name, and the leaf paths ({@link #pathKey}) of each merged object
     * declaring last, at any depth (CR-2026-173).
     */
    record Modes(Map<String, String> modes, Map<String, Set<String>> lastMembers) {
        String mode(String name) {
            return modes.getOrDefault(name, "collect");
        }

        Set<String> last(String name) {
            return lastMembers.getOrDefault(name, Set.of());
        }
    }

    static final Modes NONE = new Modes(Map.of(), Map.of());

    private OnRepeat() {}

    /**
     * The modes of a tlv block as written ({@code tag_size}, {@code cases}, ...). Throws
     * the first PS-525/PS-526 violation, so a schema breaking them is rejected at load.
     */
    static Modes of(Map<?, ?> tlv) {
        Object rawDefault = tlv.containsKey("on_repeat") ? tlv.get("on_repeat") : "collect";
        if (!(rawDefault instanceof String d) || !MODES.contains(d)) {
            throw new SchemaException("tlv `on_repeat` is one of collect, merge, last, not "
                    + pyRepr(rawDefault) + " (PS-525)");
        }
        String dflt = (String) rawDefault;
        Map<String, String> modes = new LinkedHashMap<>();
        Map<String, Set<String>> lastMembers = new LinkedHashMap<>();
        Map<List<String>, List<Map<?, ?>>> shared = new LinkedHashMap<>();
        Map<String, Set<String>> objects = new LinkedHashMap<>();   // paths combined as objects
        if (tlv.get("cases") instanceof Map<?, ?> cases) {
            for (Object body : cases.values()) {
                if (!(body instanceof List<?> list)) continue;
                for (Map.Entry<String, Map<?, ?>> e : reportedFields(list).entrySet()) {
                    String name = e.getKey();
                    Map<?, ?> field = e.getValue();
                    Object own = field.get("on_repeat");
                    if (own != null && !(own instanceof String s && MODES.contains(s))) {
                        throw new SchemaException("Field '" + name + "': `on_repeat` is one of "
                                + "collect, merge, last, not " + pyRepr(own) + " (PS-525)");
                    }
                    boolean isObject = "object".equals(field.get("type"));
                    if ("merge".equals(own) && !isObject) {
                        throw new SchemaException("Field '" + name + "': only a `type: object` "
                                + "field may declare `on_repeat: merge` (PS-526)");
                    }
                    String mode = own != null ? (String) own
                            : (!"merge".equals(dflt) || isObject ? dflt : "collect");
                    String earlier = modes.putIfAbsent(name, mode);
                    if (earlier != null && !earlier.equals(mode)) {
                        throw new SchemaException("tlv: the fields reporting '" + name
                                + "' declare different `on_repeat`, " + earlier + " and " + mode
                                + " (PS-526)");
                    }
                    if ("merge".equals(mode)) {
                        // CR-2026-173: a member that is an object is combined member by member
                        // too. PS-526 concerns two cases declaring a leaf; one case naming a
                        // member twice counts once.
                        Set<String> last = lastMembers.computeIfAbsent(name, k -> new LinkedHashSet<>());
                        Set<String> inCase = new java.util.HashSet<>();
                        for (Member m : mergedMembers(field.get("fields"), List.of())) {
                            String key = pathKey(m.path());
                            if (m.isObject()) {
                                objects.computeIfAbsent(name, k -> new LinkedHashSet<>()).add(key);
                                continue;
                            }
                            if (!inCase.add(key)) continue;
                            shared.computeIfAbsent(List.of(name, key), k -> new java.util.ArrayList<>()).add(m.decl());
                            if ("last".equals(m.decl().get("on_repeat"))) last.add(key);
                        }
                    }
                }
            }
        }
        for (Map.Entry<List<String>, List<Map<?, ?>>> e : shared.entrySet()) {
            String name = e.getKey().get(0), dotted = dotted(e.getKey().get(1));
            if (objects.getOrDefault(name, Set.of()).contains(e.getKey().get(1))) {
                throw new SchemaException("tlv: the merged object '" + name + "' declares '" + dotted
                        + "' as an object in one case and as a value in another (PS-526)");
            }
            // CR-2026-128: merged cases share a leaf only where it declares `last`, at any
            // depth (CR-2026-173).
            if (e.getValue().size() < 2) continue;
            for (Map<?, ?> declaration : e.getValue()) {
                if (!"last".equals(declaration.get("on_repeat"))) {
                    throw new SchemaException("tlv: cases share the member '" + dotted
                            + "' of the merged object '" + name
                            + "' without `on_repeat: last` (PS-526)");
                }
            }
        }
        return new Modes(modes, lastMembers);
    }

    /**
     * Every named field a case body reports, by name, first declaration first, looking
     * through the constructs whose fields merge into the enclosing object. An object's or
     * repeat's members are not descended into: they report under the field's own name.
     */
    static Map<String, Map<?, ?>> reportedFields(List<?> fields) {
        Map<String, Map<?, ?>> found = new LinkedHashMap<>();
        walk(fields, found);
        return found;
    }

    private static void walk(Object items, Map<String, Map<?, ?>> found) {
        if (!(items instanceof List<?> list)) return;
        for (Object o : list) {
            if (!(o instanceof Map<?, ?> item)) continue;
            if (item.containsKey("type")) {
                if (item.get("name") instanceof String n && !n.isEmpty()) found.putIfAbsent(n, item);
                walk(item.get("trailer"), found);
                continue;
            }
            Object spec = null;
            for (String k : REPORTING) {
                if (item.containsKey(k)) {
                    spec = item.get(k);
                    break;
                }
            }
            if (spec instanceof List) {
                walk(spec, found);
            } else if (spec instanceof Map<?, ?> m) {
                walk(m.get("fields"), found);
                if (m.get("cases") instanceof Map<?, ?> cases) {
                    for (Object body : cases.values()) walk(body, found);
                }
                if (m.get("groups") instanceof List<?> groups) {
                    for (Object g : groups) {
                        if (g instanceof Map<?, ?> gm) walk(gm.get("fields"), found);
                    }
                }
            }
        }
    }

    /**
     * {@code last} keeps the later value; {@code merge} combines two objects member by
     * member, recursing where both carry an object (CR-2026-173), and a leaf reported twice
     * is an error unless it declares {@code on_repeat: last} (PS-525). {@code lastMembers}
     * holds leaf paths.
     */
    static Object combine(String name, Object earlier, Object value, String mode, Set<String> lastMembers) {
        if ("last".equals(mode)) return value;
        return merge(name, earlier, value, lastMembers, List.of());
    }

    @SuppressWarnings("unchecked")
    private static Object merge(String name, Object earlier, Object value, Set<String> lastMembers,
                                List<String> path) {
        Map<String, Object> combined = new LinkedHashMap<>((Map<String, Object>) earlier);
        for (Map.Entry<String, Object> e : ((Map<String, Object>) value).entrySet()) {
            List<String> here = new java.util.ArrayList<>(path);
            here.add(e.getKey());
            boolean twice = combined.containsKey(e.getKey()) && !lastMembers.contains(pathKey(here));
            if (twice && combined.get(e.getKey()) instanceof Map && e.getValue() instanceof Map) {
                combined.put(e.getKey(), merge(name, combined.get(e.getKey()), e.getValue(), lastMembers, here));
                continue;
            }
            if (twice) {
                throw new SchemaException.DecodeException("tlv: the merged object '" + name
                        + "' reports its member '" + String.join(".", here) + "' twice (PS-525)");
            }
            combined.put(e.getKey(), e.getValue());
        }
        return combined;
    }

    /** One named member of a merged object, at any depth (CR-2026-173). */
    record Member(List<String> path, Map<?, ?> decl, boolean isObject) {}

    /**
     * merged_members: each named member of a merged object with its path, where a member
     * of {@code type: object} not declaring {@code on_repeat: last} is combined member by
     * member and anything else is a leaf.
     */
    static List<Member> mergedMembers(Object fields, List<String> path) {
        List<Member> out = new java.util.ArrayList<>();
        if (!(fields instanceof List<?> list)) return out;
        for (Object o : list) {
            if (!(o instanceof Map<?, ?> m) || !(m.get("name") instanceof String n) || n.isEmpty()) continue;
            List<String> here = new java.util.ArrayList<>(path);
            here.add(n);
            boolean isObject = "object".equals(m.get("type")) && !"last".equals(m.get("on_repeat"));
            out.add(new Member(here, m, isObject));
            if (isObject) out.addAll(mergedMembers(m.get("fields"), here));
        }
        return out;
    }

    /** A member path as one key; a name may hold a dot. */
    static String pathKey(List<String> path) {
        return String.join("\u0000", path);
    }

    /** A path key as {@code a.b}, as a message writes it. */
    static String dotted(String key) {
        return key.replace('\u0000', '.');
    }

    /** leaf_paths: a value's leaf paths, descending only into the declared objects. */
    static void leafPaths(Map<?, ?> value, Set<String> objects, List<String> path, Set<String> out) {
        for (Map.Entry<?, ?> e : value.entrySet()) {
            List<String> here = new java.util.ArrayList<>(path);
            here.add(String.valueOf(e.getKey()));
            if (e.getValue() instanceof Map<?, ?> sub && objects.contains(pathKey(here))) {
                leafPaths(sub, objects, here, out);
            } else {
                out.add(pathKey(here));
            }
        }
    }

    /** value_at: the part of a nested value holding exactly the given leaf paths. */
    @SuppressWarnings("unchecked")
    static Map<String, Object> valueAt(Map<?, ?> value, Set<String> keys) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (String key : keys) {
            String[] path = key.split("\u0000", -1);
            Map<?, ?> src = value;
            Map<String, Object> dst = out;
            for (int i = 0; i < path.length - 1; i++) {
                src = (Map<?, ?>) src.get(path[i]);
                dst = (Map<String, Object>) dst.computeIfAbsent(path[i], k -> new LinkedHashMap<String, Object>());
            }
            dst.put(path[path.length - 1], src.get(path[path.length - 1]));
        }
        return out;
    }

    /**
     * A merged object field's leaf paths and the paths it combines as objects, read from
     * its typed members (CR-2026-173).
     */
    static void typedMembers(List<Field> fields, List<String> path, Set<String> leaves, Set<String> objects) {
        if (fields == null) return;
        for (Field m : fields) {
            if (m.getName() == null || m.getName().isEmpty()) continue;
            List<String> here = new java.util.ArrayList<>(path);
            here.add(m.getName());
            boolean last = m.getRaw() != null && "last".equals(m.getRaw().get("on_repeat"));
            if (m.getType() == FieldType.OBJECT && !last) {
                if (objects != null) objects.add(pathKey(here));
                typedMembers(m.getFields(), here, leaves, objects);
            } else if (leaves != null) {
                leaves.add(pathKey(here));
            }
        }
    }

    static Set<String> leaves(Field f) {
        Set<String> out = new LinkedHashSet<>();
        typedMembers(f.getFields(), List.of(), out, null);
        return out;
    }

    /** Python's repr of a scalar, as the reference's message writes it. */
    private static String pyRepr(Object v) {
        if (v instanceof String s) return "'" + s + "'";
        if (v == null) return "None";
        if (v instanceof Boolean b) return b ? "True" : "False";
        return String.valueOf(v);
    }

    /**
     * _reports_list: whether a field's own value is a list - a repeat, or bytes rendered
     * as an array - so a list under its name is not PS-157's collection of entries.
     */
    static boolean reportsList(Field f) {
        if (f.getType() == FieldType.REPEAT) return true;
        return f.getType() == FieldType.BYTES && "array".equals(f.getFormat());
    }

    /** Mappings whose keys are data, and `definitions`, held to the rule where spliced. */
    private static final Set<String> OPAQUE =
            Set.of("lookup", "values", "test_vectors", "metadata", "definitions");

    /**
     * PS-525 (CR-2026-125): {@code on_repeat} declared where it has no effect makes the
     * schema invalid. It belongs on a tlv, on a field a tlv case reports, and as
     * {@code last} on a member of a merged object; anything else is refused at load, in the
     * reference's wording ({@code on_repeat_schema_errors}). Declarations are compared by
     * identity, so a field is recognised as the one a case reports.
     */
    static void checkPlacement(Object root) {
        Set<Object> allowed = java.util.Collections.newSetFromMap(new java.util.IdentityHashMap<>());
        allowTlvs(root, allowed);
        refuseStrays(root, allowed);
    }

    private static void allowTlvs(Object node, Set<Object> allowed) {
        if (node instanceof List<?> list) {
            for (Object item : list) allowTlvs(item, allowed);
        } else if (node instanceof Map<?, ?> map) {
            if (map.get("tlv") instanceof Map<?, ?> tlv) {
                allowed.add(tlv);
                Modes modes = of(tlv);
                if (tlv.get("cases") instanceof Map<?, ?> cases) {
                    for (Object body : cases.values()) {
                        if (!(body instanceof List<?> fields)) continue;
                        for (Map.Entry<String, Map<?, ?>> e : reportedFields(fields).entrySet()) {
                            allowed.add(e.getValue());
                            if (!"merge".equals(modes.mode(e.getKey()))) continue;
                            // A merged object's leaf may declare `last` at any depth
                            // (PS-525, CR-2026-173).
                            for (Member m : mergedMembers(e.getValue().get("fields"), List.of())) {
                                if ("last".equals(m.decl().get("on_repeat"))) allowed.add(m.decl());
                            }
                        }
                    }
                }
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!OPAQUE.contains(String.valueOf(e.getKey()))) allowTlvs(e.getValue(), allowed);
            }
        }
    }

    private static void refuseStrays(Object node, Set<Object> allowed) {
        if (node instanceof List<?> list) {
            for (Object item : list) refuseStrays(item, allowed);
        } else if (node instanceof Map<?, ?> map) {
            if (map.containsKey("on_repeat") && !allowed.contains(map)) {
                Object name = map.get("name");
                throw new SchemaException("`on_repeat` on '" + (name != null ? name : "an entry")
                        + "' has no effect there: it belongs on a tlv, a field of a tlv case, or a"
                        + " merged object's member (PS-525)");
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!OPAQUE.contains(String.valueOf(e.getKey()))) refuseStrays(e.getValue(), allowed);
            }
        }
    }
}
