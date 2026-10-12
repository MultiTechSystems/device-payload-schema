package org.lora.schema;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 0.5.2 wave 6a: the repeat iterator and reserved trailers (CR-2026-048, -053, -054, -055,
 * -080, -081). The schema rules, checked once over the raw document after every
 * {@code $ref} is spliced. Each method mirrors the function of the same purpose in
 * tools/schema_interpreter.py: {@code iterator_errors}, {@code _repeat_member_ref_errors},
 * {@code tlv_reserve_errors}, {@code repeat_only_names} and {@code fixed_element_size}.
 *
 * <p>A walk of its own rather than a check in {@code parseFields}, because PS-369 needs the
 * names of every enclosing repeat and PS-381 whether a field sits directly in a repeat's
 * elements - neither is known to a check that sees one list member at a time.
 */
final class Wave6a {
    private Wave6a() {}

    private static final List<String> ITERATOR_KEYS = List.of("index", "count_as");
    private static final List<String> GUARD_OPS =
            List.of("gt", "gte", "lt", "lte", "eq", "ne", "in", "not_in");     // PS-126, PS-543
    private static final List<String> SET_OPS = List.of("in", "not_in");
    private static final List<String> COMPUTED_TYPES = List.of("number", "integer");
    private static final Pattern TEMPLATE_REF = Pattern.compile("\\$\\{([^}]+)\\}");

    /** Rejects the schema when any wave 6a rule fails, naming every failure. */
    static void checkSchema(Map<String, Object> raw) {
        List<String> errors = schemaErrors(raw);
        if (!errors.isEmpty()) {
            throw new SchemaException(String.join("; ", errors));
        }
    }

    /** iterator_errors over the top-level and port field lists, and every tlv's reserve. */
    static List<String> schemaErrors(Map<String, Object> raw) {
        List<String> errors = new ArrayList<>(iteratorErrors(raw.get("fields"), List.of(), false, "fields"));
        if (raw.get("ports") instanceof Map<?, ?> ports) {
            for (Map.Entry<?, ?> e : ports.entrySet()) {
                Object group = e.getValue() instanceof Map<?, ?> entry ? entry.get("fields") : e.getValue();
                errors.addAll(iteratorErrors(group, List.of(), false, "ports[" + e.getKey() + "].fields"));
            }
        }
        errors.addAll(ipsoInstanceErrors(raw));                             // PS-548
        collectTlvReserveErrors(raw, errors);
        errors.addAll(recordsErrors(raw));                                  // PS-541
        collectConditionErrors(raw, errors);                                // PS-543
        return errors;
    }

    /** A construct entry: a mapping carrying {@code key} and no {@code type}. */
    private static boolean isConstruct(Map<?, ?> item, String key) {
        return item.containsKey(key) && item.get("type") == null;
    }

    /**
     * Whether a field list reaches a {@code records} entry (PS-541): directly, or through a
     * match case or default, which merge into the list. Mirrors records_reachable.
     */
    static boolean recordsReachable(Object fieldsRaw) {
        if (!(fieldsRaw instanceof List<?> fields)) return false;
        for (Object o : fields) {
            if (!(o instanceof Map<?, ?> item)) continue;
            if (isConstruct(item, "records")) return true;
            if (isConstruct(item, "match") && item.get("match") instanceof Map<?, ?> match) {
                List<Object> bodies = new ArrayList<>();
                if (match.get("cases") instanceof Map<?, ?> cases) bodies.addAll(cases.values());
                bodies.add(match.get("default"));
                for (Object body : bodies) {
                    if (body instanceof List<?> && recordsReachable(body)) return true;
                }
            }
        }
        return false;
    }

    /**
     * PS-541: a {@code records} entry is a non-empty list of field lists, in a repeat's
     * element or a match case within it, and never reached from an {@code element}
     * (PS-528). Mirrors records_errors, with its wording.
     */
    static List<String> recordsErrors(Map<String, Object> raw) {
        List<String> errors = new ArrayList<>();
        walkRecords(raw.get("fields"), false, errors);
        if (raw.get("ports") instanceof Map<?, ?> ports) {
            for (Object entry : ports.values()) {
                walkRecords(entry instanceof Map<?, ?> e ? e.get("fields") : entry, false, errors);
            }
        }
        return errors;
    }

    private static void walkRecords(Object fieldsRaw, boolean inElement, List<String> errors) {
        if (!(fieldsRaw instanceof List<?> fields)) return;
        for (Object o : fields) {
            if (!(o instanceof Map<?, ?> item)) continue;
            if (isConstruct(item, "records")) {
                if (!inElement) {
                    errors.add("`records` belongs in a repeat's element, or a match case within it (PS-541)");
                }
                boolean wellFormed = item.get("records") instanceof List<?> bodies && !bodies.isEmpty();
                if (wellFormed) {
                    for (Object body : (List<?>) item.get("records")) {
                        if (!(body instanceof List)) wellFormed = false;
                    }
                }
                if (!wellFormed) {
                    errors.add("`records` is a non-empty list of field lists (PS-541)");
                    continue;
                }
                for (Object body : (List<?>) item.get("records")) walkRecords(body, false, errors);
                continue;
            }
            if ("repeat".equals(item.get("type"))) {
                if (item.get("element") instanceof Map<?, ?> element) {
                    if (recordsReachable(List.of(element))) {
                        errors.add("Repeat '" + (item.containsKey("name") ? item.get("name") : "<unnamed>")
                                + "': an `element` cannot reach a `records` entry (PS-541)");
                    } else {
                        walkRecords(List.of(element), false, errors);
                    }
                }
                walkRecords(item.get("fields"), true, errors);
                walkRecords(item.get("trailer"), false, errors);
                List<String> clash = recordsClash(item.get("fields"));
                if (!clash.isEmpty()) {
                    StringBuilder listed = new StringBuilder("[");
                    for (int i = 0; i < clash.size(); i++) {
                        if (i > 0) listed.append(", ");
                        listed.append('\'').append(clash.get(i)).append('\'');
                    }
                    listed.append(']');
                    errors.add("Repeat '" + (item.containsKey("name") ? item.get("name") : "<unnamed>")
                            + "': " + listed + " is reported both by a record and by the element"
                            + " outside `records` (PS-541)");
                }
                continue;
            }
            if (isConstruct(item, "match") && item.get("match") instanceof Map<?, ?> match) {
                if (match.get("cases") instanceof Map<?, ?> cases) {
                    for (Object body : cases.values()) walkRecords(body, inElement, errors);
                }
                walkRecords(match.get("default"), inElement, errors);
            }
            if (item.get("cases") instanceof Map<?, ?> cases) {
                for (Object body : cases.values()) walkRecords(body, false, errors);
            }
            walkRecords(item.get("fields"), false, errors);
            if (item.get("tlv") instanceof Map<?, ?> tlv && tlv.get("cases") instanceof Map<?, ?> cases) {
                for (Object body : cases.values()) walkRecords(body, false, errors);
            }
            if (item.get("flagged") instanceof Map<?, ?> flagged && flagged.get("groups") instanceof List<?> groups) {
                for (Object g : groups) {
                    if (g instanceof Map<?, ?> group) walkRecords(group.get("fields"), false, errors);
                }
            }
            Object group = item.get("byte_group");
            walkRecords(group instanceof Map<?, ?> g ? g.get("fields") : group, false, errors);
        }
    }

    /**
     * The names a field list reports at its own level: named fields, and those of the
     * constructs merging into it (match and tlv cases and default, flagged groups,
     * byte_group members). An object or repeat contributes its own name; internal and
     * name_from fields, and {@code records} bodies, contribute none. Mirrors level_names.
     */
    static java.util.Set<String> levelNames(Object fieldsRaw) {
        java.util.Set<String> found = new java.util.TreeSet<>();
        if (!(fieldsRaw instanceof List<?> fields)) return found;
        for (Object o : fields) {
            if (!(o instanceof Map<?, ?> item)) continue;
            if (isConstruct(item, "records")) continue;
            Object type = item.get("type");
            if (type != null && !"".equals(type)) {
                if (item.get("name") instanceof String name && !name.startsWith("_")
                        && item.get("name_from") == null) {
                    found.add(name);
                }
                continue;
            }
            for (String key : List.of("match", "tlv")) {
                if (item.get(key) instanceof Map<?, ?> spec) {
                    if (spec.get("cases") instanceof Map<?, ?> cases) {
                        for (Object body : cases.values()) found.addAll(levelNames(body));
                    }
                    found.addAll(levelNames(spec.get("default")));
                }
            }
            if (item.get("flagged") instanceof Map<?, ?> flagged && flagged.get("groups") instanceof List<?> groups) {
                for (Object g : groups) {
                    if (g instanceof Map<?, ?> group) found.addAll(levelNames(group.get("fields")));
                }
            }
            Object group = item.get("byte_group");
            found.addAll(levelNames(group instanceof Map<?, ?> g ? g.get("fields") : group));
        }
        return found;
    }

    /**
     * PS-541 (CR-2026-124): the names a record shares with a field the element reports
     * outside {@code records}, sorted. Mirrors records_clash.
     */
    static List<String> recordsClash(Object fieldsRaw) {
        java.util.Set<String> outside = levelNames(fieldsRaw);
        java.util.Set<String> inside = new java.util.TreeSet<>();
        collectRecordNames(fieldsRaw, inside);
        inside.retainAll(outside);
        return new ArrayList<>(inside);
    }

    private static void collectRecordNames(Object fieldsRaw, java.util.Set<String> inside) {
        if (!(fieldsRaw instanceof List<?> fields)) return;
        for (Object o : fields) {
            if (!(o instanceof Map<?, ?> item)) continue;
            if (isConstruct(item, "records") && item.get("records") instanceof List<?> bodies) {
                for (Object body : bodies) inside.addAll(levelNames(body));
            }
            if (isConstruct(item, "match") && item.get("match") instanceof Map<?, ?> match) {
                if (match.get("cases") instanceof Map<?, ?> cases) {
                    for (Object body : cases.values()) collectRecordNames(body, inside);
                }
                collectRecordNames(match.get("default"), inside);
            }
        }
    }

    /**
     * PS-543: a condition's {@code in} or {@code not_in} is a list of numbers, and the
     * condition's one operator (PS-126). Over every guard condition and present_if, as
     * condition_errors does.
     */
    private static void collectConditionErrors(Object node, List<String> errors) {
        if (node instanceof Map<?, ?> map) {
            Object name = map.containsKey("name") ? map.get("name") : "?";
            checkCondition(map.get("present_if"), "the present_if of '" + name + "'", errors);
            if (map.get("guard") instanceof Map<?, ?> guard && guard.get("when") instanceof List<?> when) {
                for (Object cond : when) checkCondition(cond, "a guard condition of '" + name + "'", errors);
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!"test_vectors".equals(e.getKey())) collectConditionErrors(e.getValue(), errors);
            }
        } else if (node instanceof List<?> list) {
            for (Object item : list) collectConditionErrors(item, errors);
        }
    }

    private static void checkCondition(Object raw, String where, List<String> errors) {
        if (!(raw instanceof Map<?, ?> cond)) return;
        int ops = 0;
        for (String op : GUARD_OPS) {
            if (cond.containsKey(op)) ops++;
        }
        if (ops != 1) {                                                     // PS-126
            errors.add(where + " has " + ops + " comparison operators; a condition has exactly one (PS-126)");
        }
        for (String op : SET_OPS) {
            if (!cond.containsKey(op)) continue;
            boolean numbers = cond.get(op) instanceof List<?> values;
            if (numbers) {
                for (Object v : (List<?>) cond.get(op)) {
                    if (!(v instanceof Number)) numbers = false;
                }
            }
            if (!numbers) errors.add("`" + op + "` in " + where + " is a list of numbers (PS-543)");
        }
    }

    private static boolean isInt(Object v) {
        return v instanceof Integer || v instanceof Long || v instanceof Short || v instanceof Byte
                || v instanceof java.math.BigInteger;
    }

    private static boolean isLiteral(Map<?, ?> f) {
        Object type = f.get("type");
        return ("string".equals(type) || "number".equals(type) || "boolean".equals(type))
                && f.containsKey("value");
    }

    /** PS-373, PS-374: a per-element unit, IPSO instance or SenML name, and what it names. */
    private static List<String> memberRefErrors(Map<?, ?> repeat, Map<?, ?> member, String where) {
        List<String> errors = new ArrayList<>();
        Map<Object, Map<?, ?>> byName = new HashMap<>();
        for (Object m : repeatMembers(repeat)) {
            if (m instanceof Map<?, ?> mm) byName.put(mm.get("name"), mm);
        }
        Object index = repeat.get("index");
        List<String[]> refs = new ArrayList<>();
        // A `$name[i]` names a sibling repeat (PS-546), which siblingErrors checks.
        if (member.get("unit") instanceof String unit && unit.startsWith("$")
                && !SIBLING_REFERENCE.matcher(unit).matches()) {
            refs.add(new String[] {"unit", unit.substring(1)});
        }
        if (member.get("ipso") instanceof Map<?, ?> ipso && ipso.get("instance") instanceof String instance
                && instance.startsWith("$") && !SIBLING_REFERENCE.matcher(instance).matches()) {
            refs.add(new String[] {"ipso.instance", instance.substring(1)});
        }
        if (member.get("senml") instanceof Map<?, ?> senml && senml.get("name") instanceof String name) {
            Matcher m = TEMPLATE_REF.matcher(name);
            while (m.find()) refs.add(new String[] {"senml.name", m.group(1)});
        }
        for (String[] ref : refs) {
            String key = ref[0], name = ref[1];
            if (name.equals(index)) {
                if (!(isInt(repeat.get("count")) || repeat.containsKey("max"))) {
                    errors.add(where + ": " + key + " uses the index '" + name + "', so the repeat needs a "
                            + "literal count or a max (PS-374)");
                }
                continue;
            }
            Map<?, ?> target = byName.get(name);
            if (target == null) {
                errors.add(where + ": " + key + " names '" + name + "', which is neither a field of the "
                        + "element nor the repeat's index (PS-373)");
            } else if (!(target.containsKey("lookup") || isLiteral(target))) {
                errors.add(where + ": " + key + " names '" + name + "', which must carry a lookup or be a "
                        + "literal so its values are known from the schema (PS-374)");
            }
        }
        return errors;
    }

    /**
     * The repeat iterator's schema rules: PS-350/PS-383/PS-384 (reserve and trailer), PS-369
     * (index and count_as names), PS-372 to PS-374 (identity and per-element references),
     * PS-381 (carry), and present_if's form (PS-386). Walks nested field lists so an
     * enclosing repeat's names are known.
     */
    static List<String> iteratorErrors(Object fieldsRaw, List<String> enclosing, boolean inElements,
                                       String where) {
        List<String> errors = new ArrayList<>();
        if (!(fieldsRaw instanceof List<?> fields)) return errors;
        Map<String, Map<?, ?>> earlier = new HashMap<>();   // PS-546: element repeats before this field
        for (int i = 0; i < fields.size(); i++) {
            if (!(fields.get(i) instanceof Map<?, ?> f)) continue;
            String at = where + "[" + i + "] (" + (f.containsKey("name") ? f.get("name") : "?") + ")";
            if (f.containsKey("carry")) {
                Object carry = f.get("carry");
                if (!inElements || !COMPUTED_TYPES.contains(f.get("type"))) {
                    errors.add(at + ": carry applies only to a computed field declared in a "
                            + "repeat's elements (PS-381)");
                } else if (!(carry instanceof Number || (carry instanceof String s && s.startsWith("$")))) {
                    errors.add(at + ": carry must be a numeric literal or a $ reference to a "
                            + "field decoded before the repeat (PS-381)");
                }
            }
            if ("repeat".equals(f.get("type"))) {
                errors.addAll(elementRepeatErrors(f));                          // PS-528, PS-530
                List<?> members = repeatMembers(f);
                String memberKey = isElementRepeat(f) ? "element" : "fields";
                Set<Object> names = new HashSet<>();
                for (Object m : members) {
                    if (m instanceof Map<?, ?> mm) names.add(mm.get("name"));
                }
                List<String> own = new ArrayList<>();
                for (String key : ITERATOR_KEYS) {
                    Object value = f.get(key);
                    if (value == null) continue;
                    if (!(value instanceof String s) || s.isEmpty()) {
                        errors.add(at + ": " + key + " must be a name (PS-366, PS-367)");
                        continue;
                    }
                    if (names.contains(s) || enclosing.contains(s) || own.contains(s)) {
                        errors.add(at + ": " + key + " '" + s + "' names a field of the element, or an "
                                + "index or count_as already in scope (PS-369)");
                    }
                    own.add(s);
                }
                if (f.containsKey("reserve")) {
                    Object reserve = f.get("reserve");
                    if (!"end".equals(f.get("until"))) {
                        errors.add(at + ": reserve applies only to a repeat with until: end (PS-350)");
                    } else if (!isInt(reserve) || ((Number) reserve).longValue() < 0) {
                        errors.add(at + ": reserve must be a non-negative integer (PS-350)");
                    }
                }
                if (f.containsKey("trailer")) {
                    Object trailer = f.get("trailer");
                    Integer size = trailer instanceof List<?> tl ? fixedElementSize(tl) : null;
                    if (!f.containsKey("reserve")) {
                        errors.add(at + ": trailer needs reserve (PS-383)");
                    } else if (size == null) {
                        errors.add(at + ": every trailer field needs a size known from the schema (PS-384)");
                    } else if (!(isInt(f.get("reserve")) && ((Number) f.get("reserve")).longValue() == size)) {
                        errors.add(at + ": the trailer's fields take " + size + " byte(s), and reserve is "
                                + f.get("reserve") + "; they must be equal (PS-384)");
                    }
                }
                Object presentIf = f.get("present_if");
                if (presentIf != null && !(presentIf instanceof Map<?, ?> p
                        && p.get("field") instanceof String
                        && GUARD_OPS.stream().anyMatch(p::containsKey))) {
                    errors.add(at + ": present_if must be one guard condition, {field: $name, <op>: value} "
                            + "(PS-386)");
                }
                errors.addAll(siblingErrors(f, earlier, at));                   // PS-546
                Object identity = f.get("identity");
                if (identity != null && !(identity.equals(f.get("index"))
                        || (identity instanceof String sid && SIBLING_REFERENCE.matcher(sid).matches())
                        || (identity instanceof String id && id.startsWith("$") && names.contains(id.substring(1))))) {
                    errors.add(at + ": identity must be the repeat's index or a $ reference to a field "
                            + "of its elements (PS-372)");
                }
                for (int j = 0; j < members.size(); j++) {
                    if (members.get(j) instanceof Map<?, ?> member) {
                        errors.addAll(memberRefErrors(f, member, at + "." + memberKey + "[" + j + "] ("
                                + (member.containsKey("name") ? member.get("name") : "?") + ")"));
                    }
                }
                List<String> deeper = new ArrayList<>(enclosing);
                deeper.addAll(own);
                errors.addAll(iteratorErrors(members, deeper, true, at + "." + memberKey));
                if (f.get("trailer") instanceof List<?> trailer) {
                    errors.addAll(iteratorErrors(trailer, enclosing, false, at + ".trailer"));
                }
                if (f.get("element") instanceof Map<?, ?> && f.get("name") instanceof String rn) {
                    earlier.put(rn, f);
                }
                continue;
            }
            if (f.get("fields") instanceof List<?> nested) {
                errors.addAll(iteratorErrors(nested, enclosing, false, at + ".fields"));
            }
            for (String construct : List.of("match", "tlv", "flagged", "byte_group")) {
                if (!(f.get(construct) instanceof Map<?, ?> body)) continue;
                Collection<?> caseLists = body.get("cases") instanceof Map<?, ?> cm ? cm.values()
                        : body.get("cases") instanceof List<?> cl ? cl : List.of();
                for (Object c : caseLists) {
                    Object group = c instanceof List<?> ? c : c instanceof Map<?, ?> cmap ? cmap.get("fields") : null;
                    errors.addAll(iteratorErrors(group, enclosing, false, at + "." + construct));
                }
                if (body.get("fields") instanceof List<?> bf) {
                    errors.addAll(iteratorErrors(bf, enclosing, false, at + "." + construct));
                }
                if (body.get("groups") instanceof List<?> groups) {
                    for (Object g : groups) {
                        if (g instanceof Map<?, ?> gm) {
                            errors.addAll(iteratorErrors(gm.get("fields"), enclosing, false, at + "." + construct));
                        }
                    }
                }
            }
        }
        return errors;
    }

    /** The name an {@code element} (PS-528) is decoded under where it declares none. */
    static final String ELEMENT_NAME = "element";

    /** A repeat that declares {@code element}, a mapping, and no {@code fields} (PS-528). */
    static boolean isElementRepeat(Map<?, ?> repeat) {
        return repeat.get("element") instanceof Map<?, ?> && !repeat.containsKey("fields");
    }

    /**
     * A repeat's element fields: its {@code fields}, or for a repeat of single values its one
     * element under its own name or {@link #ELEMENT_NAME}. Mirrors repeat_members.
     */
    static List<?> repeatMembers(Map<?, ?> repeat) {
        if (isElementRepeat(repeat)) {
            return List.of(elementMember((Map<?, ?>) repeat.get("element")));
        }
        return repeat.get("fields") instanceof List<?> l ? l : List.of();
    }

    /**
     * The one field an {@code element} decodes as: under its own name, or
     * {@link #ELEMENT_NAME}. PS-545: the element's name is never reported, so an internal
     * name hides nothing; an element named with {@code _} is decoded under ELEMENT_NAME and
     * keeps {@code $_x} bound through {@code var}.
     */
    static Map<String, Object> elementMember(Map<?, ?> element) {
        Map<String, Object> member = new LinkedHashMap<>();
        for (Map.Entry<?, ?> e : element.entrySet()) member.put(String.valueOf(e.getKey()), e.getValue());
        String name = member.get("name") instanceof String s && !s.isEmpty() ? s : ELEMENT_NAME;
        if (name.startsWith("_")) {
            if (!member.containsKey("var")) member.put("var", name);
            name = ELEMENT_NAME;
        }
        member.put("name", name);
        return member;
    }

    /** What an {@code element} may not be (PS-545): it is one data field, not a construct. */
    static final List<String> ELEMENT_CONSTRUCTS = List.of("match", "byte_group", "flagged", "tlv", "records");

    /** {@code $name[i]}: a sibling repeat's value at this position (PS-546). */
    static final Pattern SIBLING_REFERENCE = Pattern.compile("^\\$(\\w+)\\[(\\w+)\\]$");

    /**
     * The {@code $name[i]} references a repeat declares (PS-546): its identity, and its
     * elements' unit and ipso.instance, as {key, sibling, index}. Mirrors sibling_references.
     */
    static List<String[]> siblingReferences(Map<?, ?> repeat) {
        List<Object[]> refs = new ArrayList<>();
        refs.add(new Object[] {"identity", repeat.get("identity")});
        for (Object m : repeatMembers(repeat)) {
            if (m instanceof Map<?, ?> member) {
                Object instance = member.get("ipso") instanceof Map<?, ?> ipso ? ipso.get("instance") : null;
                refs.add(new Object[] {"unit", member.get("unit")});
                refs.add(new Object[] {"ipso.instance", instance});
            }
        }
        List<String[]> found = new ArrayList<>();
        for (Object[] ref : refs) {
            if (ref[1] instanceof String text) {
                Matcher m = SIBLING_REFERENCE.matcher(text);
                if (m.matches()) found.add(new String[] {(String) ref[0], m.group(1), m.group(2)});
            }
        }
        return found;
    }

    /**
     * PS-546: each {@code $name[i]} names a repeat declaring {@code element} earlier in the
     * same field list, {@code i} is this repeat's index, and the sibling's element carries a
     * lookup or is a literal (PS-374). Mirrors sibling_errors, with its wording.
     */
    static List<String> siblingErrors(Map<?, ?> repeat, Map<String, Map<?, ?>> earlier, String at) {
        List<String> errors = new ArrayList<>();
        for (String[] ref : siblingReferences(repeat)) {
            String label = at + ": " + ref[0] + " `$" + ref[1] + "[" + ref[2] + "]`";
            if (!ref[2].equals(repeat.get("index"))) {
                errors.add(label + " must index by the repeat's own `index` (PS-546)");
                continue;
            }
            Map<?, ?> sibling = earlier.get(ref[1]);
            if (sibling == null) {
                errors.add(label + " names no repeat declaring `element` earlier in the same field list (PS-546)");
                continue;
            }
            Map<?, ?> element = (Map<?, ?>) sibling.get("element");
            if (!(element.containsKey("lookup") || isLiteral(element))) {
                errors.add(label + ": the elements of '" + ref[1] + "' identify values but carry no lookup "
                        + "and are not a literal (PS-374, PS-546)");
            }
        }
        return errors;
    }

    /**
     * Name to declaration of each repeat declaring {@code element} in one field list, the
     * siblings a {@code $name[i]} can name (PS-546); match and tlv cases and flagged groups
     * are part of the list. Mirrors element_repeats.
     */
    static Map<String, Map<?, ?>> elementRepeats(Object fieldsRaw) {
        Map<String, Map<?, ?>> found = new LinkedHashMap<>();
        visitElementRepeats(fieldsRaw, found);
        return found;
    }

    private static void visitElementRepeats(Object fieldsRaw, Map<String, Map<?, ?>> found) {
        if (!(fieldsRaw instanceof List<?> items)) return;
        for (Object o : items) {
            if (!(o instanceof Map<?, ?> f)) continue;
            if ("repeat".equals(f.get("type"))) {
                if (f.get("element") instanceof Map<?, ?> && f.get("name") instanceof String n) {
                    found.putIfAbsent(n, f);
                }
                continue;
            }
            for (String construct : List.of("match", "tlv")) {
                if (f.get(construct) instanceof Map<?, ?> body) {
                    if (body.get("cases") instanceof Map<?, ?> cases) {
                        for (Object c : cases.values()) visitElementRepeats(c, found);
                    }
                    visitElementRepeats(body.get("default"), found);
                }
            }
            if (f.get("flagged") instanceof Map<?, ?> flagged && flagged.get("groups") instanceof List<?> groups) {
                for (Object g : groups) {
                    if (g instanceof Map<?, ?> gm) visitElementRepeats(gm.get("fields"), found);
                }
            }
        }
    }

    /** The largest LwM2M object instance ID; 65535 is reserved (PS-548). */
    static final long IPSO_INSTANCE_MAX = 65534;

    private static boolean validInstance(Object v) {
        return isInt(v) && ((Number) v).longValue() >= 0 && ((Number) v).longValue() <= IPSO_INSTANCE_MAX;
    }

    /**
     * PS-548: an ipso.instance is an integer from 0 to 65534, or a reference every value of
     * which is one, checked through the listing _meta would carry (PS-375, PS-547). Mirrors
     * ipso_instance_errors, with its wording.
     */
    static List<String> ipsoInstanceErrors(Map<String, Object> raw) {
        List<String> errors = new ArrayList<>();
        walkInstanceItem(raw, null, Map.of(), Map.of(), errors);
        return errors;
    }

    private static void walkInstanceList(Object node, Map<?, ?> repeat, Map<String, Map<?, ?>> siblings,
                                         List<String> errors) {
        if (!(node instanceof List<?> list)) return;
        Map<String, Map<?, ?>> here = elementRepeats(list);
        for (Object item : list) walkInstanceItem(item, repeat, siblings, here, errors);
    }

    private static void walkInstanceItem(Object item, Map<?, ?> repeat, Map<String, Map<?, ?>> siblings,
                                         Map<String, Map<?, ?>> here, List<String> errors) {
        if (!(item instanceof Map<?, ?> f)) {
            walkInstanceList(item, repeat, siblings, errors);
            return;
        }
        if (f.get("ipso") instanceof Map<?, ?> ipso && ipso.containsKey("instance")) {
            checkInstance(f, ipso.get("instance"), repeat, siblings, errors);
        }
        for (Map.Entry<?, ?> e : f.entrySet()) {
            Object key = e.getKey();
            if (List.of("test_vectors", "definitions", "ipso", "senml", "lookup").contains(key)) continue;
            Object value = e.getValue();
            if ("repeat".equals(f.get("type")) && ("fields".equals(key) || "element".equals(key))) {
                // A member's references name this repeat's index and fields, and the
                // element repeats beside it (PS-546).
                walkInstanceList(value instanceof List<?> ? value : java.util.Collections.singletonList(value),
                        f, here, errors);
            } else if (value instanceof Map<?, ?>) {
                walkInstanceItem(value, repeat, siblings, here, errors);
            } else {
                walkInstanceList(value, repeat, siblings, errors);
            }
        }
    }

    private static void checkInstance(Map<?, ?> field, Object instance, Map<?, ?> repeat,
                                      Map<String, Map<?, ?>> siblings, List<String> errors) {
        Object listing = Meta.reference(instance, repeat, false, siblings);
        boolean ok;
        if (listing instanceof Map<?, ?> l && l.containsKey("index")) {
            Object count = l.get("count");
            if (!isInt(count)) return;              // PS-374 reports an index with no literal count or max
            long low = l.get("index_base") instanceof Number b ? b.longValue() : 0;
            long high = low + Math.max(((Number) count).longValue(), 1) - 1;
            ok = low >= 0 && low <= IPSO_INSTANCE_MAX && high >= 0 && high <= IPSO_INSTANCE_MAX;
        } else if (listing instanceof Map<?, ?> l) {
            ok = !Boolean.TRUE.equals(l.get("open"));
            if (l.get("values") instanceof List<?> values) {
                for (Object v : values) {
                    if (!validInstance(v)) ok = false;
                }
            }
        } else {
            ok = validInstance(listing);
        }
        if (!ok) {
            errors.add("Field '" + (field.containsKey("name") ? field.get("name") : "<unnamed>")
                    + "': its ipso.instance can be other than an integer from 0 to " + IPSO_INSTANCE_MAX
                    + " (PS-548)");
        }
    }

    /**
     * PS-083, PS-528, PS-530: a repeat declares exactly one of {@code fields} and
     * {@code element}, an element is one field definition, and {@code index_base} is an
     * integer. The wording is the spec prototype's, as element_repeat_errors gives it.
     */
    static List<String> elementRepeatErrors(Map<?, ?> f) {
        String label = "Repeat '" + (f.containsKey("name") ? f.get("name") : "<unnamed>") + "'";
        List<String> errors = new ArrayList<>();
        boolean hasFields = f.containsKey("fields");
        if (hasFields == f.containsKey("element")) {
            errors.add(label + " declares " + (hasFields ? "both" : "neither") + " of `fields` and `element`; "
                    + "it declares exactly one (PS-083, PS-528)");
        } else if (f.containsKey("element") && !(f.get("element") instanceof Map<?, ?>)) {
            errors.add(label + ": `element` is a single field definition (PS-528)");
        } else if (f.get("element") instanceof Map<?, ?> element
                && (!element.containsKey("type") || ELEMENT_CONSTRUCTS.stream().anyMatch(element::containsKey))) {
            errors.add(label + ": `element` is a data field that declares `type`, not a construct (PS-545)");
        }
        if (f.containsKey("index_base") && !isInt(f.get("index_base"))) {
            errors.add(label + ": `index_base` is an integer (PS-530)");
        }
        return errors;
    }

    /** PS-471: a tlv's reserve is a non-negative integer. Every tlv in the document. */
    private static void collectTlvReserveErrors(Object node, List<String> errors) {
        if (node instanceof Map<?, ?> map) {
            Object type = map.get("type");
            boolean typed = type != null && !"".equals(type) && !Boolean.FALSE.equals(type);
            if (map.containsKey("tlv") && !typed && map.get("tlv") instanceof Map<?, ?> body
                    && body.containsKey("reserve")) {
                Object reserve = body.get("reserve");
                if (!isInt(reserve) || ((Number) reserve).longValue() < 0) {
                    errors.add("tlv reserve must be a non-negative integer, got " + reserve + " (PS-471)");
                }
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if ("test_vectors".equals(e.getKey()) || "definitions".equals(e.getKey())) continue;
                collectTlvReserveErrors(e.getValue(), errors);
            }
        } else if (node instanceof List<?> list) {
            for (Object item : list) collectTlvReserveErrors(item, errors);
        }
    }

    /** Names declared in some repeat's elements and nowhere outside one (PS-368). */
    static Set<String> repeatOnlyNames(Map<String, Object> raw) {
        Set<String> inside = new HashSet<>();
        Set<String> outside = new HashSet<>();
        walkNames(raw, false, inside, outside);
        inside.removeAll(outside);
        return inside;
    }

    private static void walkNames(Object node, boolean inElements, Set<String> inside, Set<String> outside) {
        if (node instanceof Map<?, ?> map) {
            if (map.get("name") instanceof String name && (map.containsKey("type") || map.containsKey("fields"))) {
                (inElements ? inside : outside).add(name);
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if ("test_vectors".equals(e.getKey()) || "definitions".equals(e.getKey())) continue;
                walkNames(e.getValue(),
                        inElements || (("fields".equals(e.getKey()) || "element".equals(e.getKey()))
                                && "repeat".equals(map.get("type"))),
                        inside, outside);
            }
        } else if (node instanceof List<?> list) {
            for (Object item : list) walkNames(item, inElements, inside, outside);
        }
    }

    private static final Map<String, Integer> FIXED_SIZES = new HashMap<>();
    static {
        String[][] sizes = {
            {"1", "u8", "uint8", "s8", "i8", "int8", "udec", "sdec"},
            {"2", "u16", "uint16", "s16", "i16", "int16", "f16", "bf16", "uflt16", "sflt16"},
            {"3", "u24", "uint24", "s24", "i24", "int24", "sflt24"},
            {"4", "u32", "uint32", "s32", "i32", "int32", "u32le16", "s32le16", "f32le16",
                "u32be16le", "s32be16le", "f32be16le", "f32"},
            {"8", "u64", "uint64", "s64", "i64", "int64", "f64"},
        };
        for (String[] row : sizes) {
            for (int k = 1; k < row.length; k++) FIXED_SIZES.put(row[k], Integer.parseInt(row[0]));
        }
    }

    /** The bytes these fields always take, or null where it varies or is zero (PS-344a, PS-384). */
    static Integer fixedElementSize(List<?> fields) {
        int total = 0;
        for (Object raw : fields) {
            if (!(raw instanceof Map<?, ?> f)) return null;
            String type = f.get("type") == null ? "" : String.valueOf(f.get("type"));
            if (FIXED_SIZES.containsKey(type)) {
                total += FIXED_SIZES.get(type);
            } else if (COMPUTED_TYPES.contains(type) || isLiteral(f)) {
                continue;
            } else if (type.contains("[") || "bool".equals(type)) {
                Object consume = f.containsKey("consume") ? f.get("consume") : 0;
                if (!isInt(consume)) return null;
                total += ((Number) consume).intValue();
            } else if (List.of("bytes", "ascii", "hex", "base64", "skip").contains(type) && isInt(f.get("length"))) {
                total += ((Number) f.get("length")).intValue();
            } else {
                return null;
            }
        }
        return total == 0 ? null : total;
    }
}
