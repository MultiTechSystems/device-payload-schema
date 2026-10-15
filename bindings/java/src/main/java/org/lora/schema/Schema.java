package org.lora.schema;

import org.yaml.snakeyaml.Yaml;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;

public class Schema {
    /**
     * Signals that a computed field is absent because its divisor was zero (PS-278).
     * A distinct NaN payload rather than a boolean flag, so it flows through the same
     * double-valued compute path without changing its signature.
     */
    private static final double COMPUTE_OMITTED = Double.longBitsToDouble(0x7ff8000000000abcL);

    private static boolean isComputeOmitted(double v) {
        return Double.doubleToRawLongBits(v) == 0x7ff8000000000abcL;
    }

    /**
     * Bit-range type, e.g. {@code u8[4:7]} - bits 4 to 7 inclusive. Since CR-2026-006
     * this is the only bitfield spelling in the language: {@code u8[3+:2]},
     * {@code bits<3,2>}, {@code bits:2@3} and {@code u8:2} were withdrawn, so there is
     * nothing left for this binding to be missing.
     */
    private static final Pattern BIT_RANGE = Pattern.compile("([us])(\\d+)\\[(\\d+):(\\d+)\\]");

    private String name;
    private int version;
    private String description;
    private String endian = "big";
    /** Applies to the whole schema where it has no ports (PS-291). */
    private String direction;
    private List<Field> fields;
    private Map<String, PortDef> ports;
    /** The document's top-level fPort (PS-458), 0 where it declares none. */
    private int docFPort;
    /** Names declared only inside some repeat's elements (PS-368). */
    private Set<String> repeatOnlyNames = Set.of();
    /** The document's {@code name} as written, null where it has none (`_meta.schema`). */
    private Object metaName;
    /** Whether the document declares {@code version}, and the value it declares. */
    private boolean declaresVersion;
    /** PS-572: each mapping and list of the expanded document, by identity, to its pointer. */
    private Map<Object, String> pointers = new java.util.IdentityHashMap<>();
    private Object declaredVersion;

    public Schema() {
        this.fields = new ArrayList<>();
    }

    // Getters and setters
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    
    public int getVersion() { return version; }
    public void setVersion(int version) { this.version = version; }
    
    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
    
    public String getEndian() { return endian; }
    public void setEndian(String endian) { this.endian = endian; }

    public String getDirection() { return direction; }
    public void setDirection(String direction) { this.direction = direction; }
    
    public List<Field> getFields() { return fields; }
    public void setFields(List<Field> fields) { this.fields = fields; }
    
    public Map<String, PortDef> getPorts() { return ports; }
    public void setPorts(Map<String, PortDef> ports) { this.ports = ports; }

    /** Plain scalars that YAML 1.1 reads as booleans and YAML 1.2 as strings (PS-575). */
    private static final Set<String> YAML11_BOOLEANS = Set.of(
        "yes", "Yes", "YES", "no", "No", "NO", "on", "On", "ON", "off", "Off", "OFF");

    /**
     * CR-2026-164 (PS-575): reject a plain (unquoted) scalar whose text is a YAML 1.1 boolean,
     * found in the event stream before any value is constructed. {@code y} and {@code n} are
     * field names and stay valid. Text that is not well-formed YAML is left to the loader.
     */
    static void checkYamlBooleans(String yamlContent) {
        try {
            for (org.yaml.snakeyaml.events.Event event : new Yaml().parse(new StringReader(yamlContent))) {
                if (event instanceof org.yaml.snakeyaml.events.ScalarEvent scalar
                        && scalar.getScalarStyle() == org.yaml.snakeyaml.DumperOptions.ScalarStyle.PLAIN
                        && YAML11_BOOLEANS.contains(scalar.getValue())) {
                    throw new SchemaException("Line " + (scalar.getStartMark().getLine() + 1)
                        + ": the unquoted value '" + scalar.getValue() + "' is a boolean in YAML 1.1"
                        + " and a string in YAML 1.2; quote it (\"" + scalar.getValue()
                        + "\") or write true or false (PS-575)");
                }
            }
        } catch (org.yaml.snakeyaml.error.YAMLException e) {
            // Not well-formed YAML: the loader that follows reports it.
        }
    }

    public static Schema fromYaml(String yamlContent) {
        checkYamlBooleans(yamlContent);
        Yaml yaml = new Yaml();
        Map<String, Object> raw = yaml.load(yamlContent);
        return parseRaw(raw);
    }

    public static Schema fromYamlFile(Path path) throws IOException {
        String content = Files.readString(path);
        return fromYaml(content);
    }

    public static Schema fromYamlFile(String path) throws IOException {
        return fromYamlFile(Path.of(path));
    }

    @SuppressWarnings("unchecked")
    private static Schema parseRaw(Map<String, Object> raw) {
        // `_meta.version` is the version the document declares, never a default (PS-175).
        boolean declaresVersion = raw.containsKey("version");
        Object declaredVersion = raw.get("version");
        // CR-2026-045: splice every $ref first, rejecting what cannot be (PS-345 to PS-349).
        raw = expandRawRefs(raw);
        // PS-510: the definitions' fields too; the spliced ones are checked in parseFields.
        if (raw.get("definitions") instanceof Map<?, ?> definitions) {
            for (Object definition : definitions.values()) checkDefinitionAnnotations(definition);
        }
        // PS-491 (CR-2026-096): one reported name's declarations in one field list agree on
        // unit, senml and ipso, so its `_meta` entry does not depend on which one decoded.
        List<String> metaErrors = Meta.declarationErrors(raw);
        if (!metaErrors.isEmpty()) {
            throw new SchemaException(metaErrors.get(0));
        }
        // Wave 6a (CR-2026-048, -053, -054, -055, -081): the iterator's and the reserves'
        // schema rules, over the whole document, since PS-369 and PS-381 depend on which
        // repeats enclose a field.
        Wave6a.checkSchema(raw);
        // Wave 6b (CR-2026-059, -067): optional fields form the tail of their list (PS-404)
        // and no field takes a name reserved for interpreter metadata (PS-433).
        Wave6b.checkSchema(raw);
        // CR-2026-110/125: PS-525 and PS-526 over every tlv, and an `on_repeat` where it
        // has no effect, refused wherever it sits.
        OnRepeat.checkPlacement(raw);
        // CR-2026-117: every `$` reference names something the schema declares (PS-539).
        References.check(raw);
        // CR-2026-138: every field but a skip has a name (PS-553). Checked after the rules
        // above, as the spec prototype does, so a construct's own error keeps its text.
        FieldNames.check(raw);
        // CR-2026-156: `sampled` only on a repeat, with a usable interval, order and anchor (PS-569).
        Sampled.check(raw);
        // CR-2026-174: a checksum on an unsigned integer wide enough for its algorithm (PS-585).
        Checksum.check(raw);
        // CR-2026-161: a lookup's labels are of one type (PS-571).
        Reports.check(raw);
        Schema schema = new Schema();
        schema.pointers = DecodeRecord.pointerMap(raw);                // PS-572
        schema.repeatOnlyNames = Wave6a.repeatOnlyNames(raw);
        schema.metaName = raw.get("name");
        schema.declaresVersion = declaresVersion;
        schema.declaredVersion = declaredVersion;

        schema.name = (String) raw.getOrDefault("name", "unnamed");
        schema.version = toInt(raw.get("version"), 1);
        schema.description = (String) raw.get("description");
        schema.endian = (String) raw.getOrDefault("endian", "big");
        schema.direction = (String) raw.get("direction");
        checkPortDeclarations(raw);
        Cr159.checkPorts(raw);             // CR-2026-159
        for (String key : new String[] {"fPort", "fport"}) {
            if (raw.get(key) instanceof Integer n) schema.docFPort = n;
        }

        // Parse fields, splicing any `$ref` into the list first.
        Object fieldsRaw = raw.get("fields");
        if (fieldsRaw instanceof List) {
            schema.fields = parseFields((List<Map<String, Object>>) fieldsRaw);
        }
        
        // No `header:` block. It was never in the specification, and honouring it
        // here while Python and Go ignored it meant the same schema decoded
        // differently per language - silently, since the ignoring implementations
        // read the header's bytes as the first fields rather than erroring. Use a
        // `definitions:` entry and `$ref` instead, which is specified and works
        // everywhere; schemas/library/common/headers.yaml does exactly that.

        // Parse ports
        Object portsRaw = raw.get("ports");
        if (portsRaw instanceof Map) {
            schema.ports = new HashMap<>();
            Map<?, ?> portsMap = (Map<?, ?>) portsRaw;
            for (Map.Entry<?, ?> entry : portsMap.entrySet()) {
                String portKey = String.valueOf(entry.getKey());
                if (entry.getValue() instanceof Map) {
                    PortDef pd = parsePortDef((Map<String, Object>) entry.getValue(), raw);
                    schema.ports.put(portKey, pd);
                }
            }
        }
        
        return schema;
    }

    @SuppressWarnings("unchecked")
    private static PortDef parsePortDef(Map<String, Object> raw, Map<String, Object> root) {
        PortDef pd = new PortDef();
        pd.setDirection((String) raw.get("direction"));
        pd.setDescription((String) raw.get("description"));

        Object fieldsRaw = raw.get("fields");
        if (fieldsRaw instanceof List) {
            pd.setFields(parseFields((List<Map<String, Object>>) fieldsRaw));
        }

        return pd;
    }

    @SuppressWarnings("unchecked")
    private static List<Field> parseFields(List<Map<String, Object>> fieldsRaw) {
        List<Field> fields = new ArrayList<>();
        if (fieldsRaw == null) return fields;
        // Names (and variables) this list declared an object or repeat under: a key
        // template cannot write one (PS-532).
        java.util.Set<String> aggregates = new java.util.HashSet<>();

        for (Map<String, Object> fm : fieldsRaw) {
            // PS-466 (CR-2026-074): the `object:` key is withdrawn; a nested group is
            // `type: object`.
            if (fm.containsKey("object") && fm.get("type") == null) {
                throw new SchemaException("the `object:` key is withdrawn; write `type: object` "
                        + "with `name: " + fm.get("object") + "` and `fields` (PS-466)");
            }
            // PS-334: a field carrying no construct needs a type; none is supplied.
            // Checked here, on list members, because parseField also parses construct
            // bodies such as an inline `tlv:` block, which carry no type.
            Object rawType = fm.get("type");
            if ((rawType == null || String.valueOf(rawType).isBlank()) && !hasConstruct(fm)) {
                throw new SchemaException("Field '" + fm.get("name") + "' declares no type");
            }
            Cr159.checkField(fm);              // CR-2026-159
            checkBytesFormat(fm);
            if (fm.containsKey("byte_group")) checkByteGroup(fm);        // PS-070, PS-397
            checkCaseBodies(fm);               // PS-441
            checkLiteral(fm);
            checkAnnotations(fm);              // PS-510
            checkWave4(fm);
            Wave5.checkLookupTemplate(fm);     // PS-407
            // PS-399, PS-414, PS-416, PS-427, PS-428: the match's discriminator source,
            // sentinel and out_of_range.
            Wave6b.checkField(fm);
            checkArithmetic(fm);               // PS-445, PS-452
            if (fm.get("name_from") instanceof String template) {
                java.util.regex.Matcher refs = NAME_FROM_PATTERN.matcher(template);
                while (refs.find()) {
                    if (aggregates.contains(refs.group(1))) {
                        throw new SchemaException("Field '" + fm.get("name") + "': "
                                + Wave5.templateShapeError(template, refs.group(1)));
                    }
                }
            }
            if ("object".equals(rawType) || "repeat".equals(rawType)) {
                if (fm.get("name") instanceof String n && !n.isEmpty()) aggregates.add(n);
                if (fm.get("var") instanceof String v && !v.isEmpty()) aggregates.add(v);
            }
            fields.add(parseField(fm));
        }
        return fields;
    }

    /**
     * A tlv block's cases, insertion-ordered: encoding ranks the candidate cases for a
     * channel and needs that ranking to be reproducible, which a HashMap's iteration order
     * is not, and PS-527 writes a merged object's entries in case declaration order. Its
     * {@code on_repeat} modes are resolved here, so a schema breaking PS-525/PS-526 is
     * rejected at load.
     */
    @SuppressWarnings("unchecked")
    private static void parseTlvCases(Field f, Map<String, Object> fm) {
        if (!(fm.get("cases") instanceof Map<?, ?> casesRaw)) return;
        Map<String, List<Field>> tlvCases = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : casesRaw.entrySet()) {
            String key = String.valueOf(entry.getKey());
            if (entry.getValue() instanceof List) {
                tlvCases.put(key, parseFields((List<Map<String, Object>>) entry.getValue()));
            }
        }
        f.setTlvCases(tlvCases);
        f.setRepeatModes(OnRepeat.of(fm));
    }

    /**
     * PS-347, PS-441: a {@code match} or {@code tlv} case body is a field list, {@code []}
     * for a case that reads nothing. A bare string such as {@code 5: skip} was skipped by
     * the parser, so a match had no case for the value and a tlv skipped the tag.
     */
    private static void checkCaseBodies(Map<String, Object> fm) {
        for (String construct : List.of("match", "tlv")) {
            if (!(fm.get(construct) instanceof Map<?, ?> body)) continue;
            if (!(body.get("cases") instanceof Map<?, ?> cases)) continue;
            for (Map.Entry<?, ?> entry : cases.entrySet()) {
                if (!(entry.getValue() instanceof List)) {
                    throw new SchemaException(construct + ".cases[" + entry.getKey()
                            + "]: a case body is a field list; write [] for a case that reads "
                            + "nothing (PS-441)");
                }
                String problem = entry.getKey() instanceof String key ? RangeKey.error(key) : null;
                if (problem != null) {
                    throw new SchemaException(construct + ".cases[" + entry.getKey() + "]: "
                            + problem);
                }
            }
        }
    }

    private static final String REF_PREFIX = "#/definitions/";

    /** The three kinds of definition (PS-345, CR-2026-168), in the order a message lists them. */
    private static final List<String> DEFINITION_KINDS = List.of("fields", "field", "lookup");

    /**
     * CR-2026-045 and CR-2026-168: resolve every reference to {@code definitions} before
     * anything is parsed. A definition carries exactly one of {@code fields} (a group),
     * {@code field} (one field) or {@code lookup} (a table) (PS-345). A
     * {@code {$ref: '#/definitions/<name>'}} in a field list is replaced by the group's
     * fields, or by the one field with each key the reference carries replacing the defined
     * key whole (PS-346, PS-578); a field's {@code lookup} given as a pointer becomes the
     * table (PS-579). References resolve transitively (PS-580), and the definitions kept are
     * themselves resolved. Rejected at load, in the spec prototype's words: a definition of
     * no kind, several or the wrong shape (PS-345), a reference that does not resolve or
     * names the wrong kind (PS-348) or another document (PS-462, optional and not supported
     * here), a group reference with keys or a nameless result (PS-578), a pointer of
     * another form (PS-461), a cycle (PS-349). Mirrors expand_refs in
     * tools/schema_interpreter.py.
     */
    @SuppressWarnings("unchecked")
    private static Map<String, Object> expandRawRefs(Map<String, Object> raw) {
        Object defsRaw = raw.get("definitions");
        if (defsRaw != null && !(defsRaw instanceof Map)) {
            throw new SchemaException("`definitions` must map names to definitions (PS-345)");
        }
        Map<String, Object> definitions = new LinkedHashMap<>();
        if (defsRaw != null) {
            for (Map.Entry<?, ?> e : ((Map<?, ?>) defsRaw).entrySet()) {
                definitions.put(String.valueOf(e.getKey()), e.getValue());
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : raw.entrySet()) {
            boolean carried = e.getKey().equals("definitions") || e.getKey().equals("test_vectors");
            out.put(e.getKey(), carried ? e.getValue() : expandNode(e.getValue(), definitions, new ArrayList<>()));
        }
        // Every definition is checked even where unreferenced; the kept ones are resolved.
        Map<String, Object> resolved = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : definitions.entrySet()) {
            String name = e.getKey();
            String kind = definitionKind(definitions, name, List.of());
            Map<String, Object> entry = new LinkedHashMap<>();
            for (Map.Entry<?, ?> k : ((Map<?, ?>) e.getValue()).entrySet()) {
                entry.put(String.valueOf(k.getKey()), k.getValue());
            }
            if (!kind.equals("lookup")) {
                entry.put(kind, expandNode(entry.get(kind), definitions, List.of(name)));
            }
            resolved.put(name, entry);
        }
        if (defsRaw != null) out.put("definitions", resolved);
        return out;
    }

    /** The kind of definition {@code name} is (PS-345), checked for a cycle and existence. */
    private static String definitionKind(Map<String, Object> definitions, String name, List<String> chain) {
        if (chain.contains(name)) {
            List<String> path = new ArrayList<>(chain);
            path.add(name);
            throw new SchemaException("`$ref` cycle: " + String.join(" -> ", path) + " (PS-349)");
        }
        if (!definitions.containsKey(name)) {
            throw new SchemaException("`$ref` to '" + REF_PREFIX + name
                    + "', which `definitions` does not define (PS-348)");
        }
        Object group = definitions.get(name);
        List<String> kinds = new ArrayList<>();
        for (String kind : DEFINITION_KINDS) {
            if (group instanceof Map<?, ?> g && g.containsKey(kind)) kinds.add(kind);
        }
        if (kinds.size() != 1) {
            String listed = kinds.isEmpty() ? "none" : Sampled.repr(kinds);
            throw new SchemaException("definition '" + name + "' must carry exactly one of `fields`, "
                    + "`field` or `lookup`, not " + listed + " (PS-345)");
        }
        String kind = kinds.get(0);
        Object body = ((Map<?, ?>) group).get(kind);
        boolean ok = switch (kind) {
            case "fields" -> body instanceof List;
            case "field" -> body instanceof Map;
            default -> body instanceof Map || body instanceof List;
        };
        if (!ok) {
            String described = switch (kind) {
                case "fields" -> "a list of fields";
                case "field" -> "a field";
                default -> "a lookup table";
            };
            throw new SchemaException("definition '" + name + "': `" + kind + "` must be " + described + " (PS-345)");
        }
        return kind;
    }

    private static List<String> deeper(List<String> chain, String name) {
        List<String> out = new ArrayList<>(chain);
        out.add(name);
        return out;
    }

    @SuppressWarnings("unchecked")
    private static Object expandNode(Object node, Map<String, Object> definitions, List<String> stack) {
        if (node instanceof Map<?, ?> map) {
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : map.entrySet()) {
                String key = String.valueOf(e.getKey());
                if (key.equals("lookup") && e.getValue() instanceof String pointer) {
                    out.put(key, lookupTable(pointer, definitions, stack));          // PS-579
                } else {
                    out.put(key, expandNode(e.getValue(), definitions, stack));
                }
            }
            return out;
        }
        if (node instanceof List<?> list) {
            List<Object> out = new ArrayList<>();
            for (Object item : list) {
                if (item instanceof Map<?, ?> m && m.containsKey("$ref")) {
                    out.addAll(resolveRef((Map<String, Object>) m, definitions, stack));
                } else {
                    out.add(expandNode(item, definitions, stack));
                }
            }
            return out;
        }
        return node;
    }

    private static String refTarget(Object ref) {
        if (ref instanceof String text && text.startsWith(REF_PREFIX)) {
            String name = text.substring(REF_PREFIX.length());
            if (!name.isEmpty() && !name.contains("/")) return name;
        }
        if (ref instanceof String text && text.contains("#") && !text.startsWith("#")) {
            throw new SchemaException("$ref " + Sampled.repr(text) + " names another document; this "
                    + "implementation resolves only #/definitions/<name> (PS-462)");
        }
        throw new SchemaException("$ref " + Sampled.repr(ref) + " is not of the form #/definitions/<name> (PS-461)");
    }

    @SuppressWarnings("unchecked")
    private static List<Object> resolveRef(Map<String, Object> item, Map<String, Object> definitions, List<String> stack) {
        String name = refTarget(item.get("$ref"));
        String kind = definitionKind(definitions, name, stack);
        Map<String, Object> overrides = new LinkedHashMap<>();
        for (Map.Entry<?, ?> e : item.entrySet()) {
            if (!"$ref".equals(String.valueOf(e.getKey()))) overrides.put(String.valueOf(e.getKey()), e.getValue());
        }
        Map<?, ?> group = (Map<?, ?>) definitions.get(name);
        if (kind.equals("lookup")) {
            throw new SchemaException("`$ref` to '" + REF_PREFIX + name + "' in a field list names a "
                    + "lookup table, not a field or a field group (PS-346, PS-348)");
        }
        if (kind.equals("fields")) {
            if (!overrides.isEmpty()) {
                throw new SchemaException("a `$ref` to the field group '" + name + "' carries nothing else: "
                        + Sampled.repr(item) + " (PS-578)");
            }
            return (List<Object>) expandNode(group.get("fields"), definitions, deeper(stack, name));
        }
        Map<String, Object> field = (Map<String, Object>) expandNode(group.get("field"), definitions, deeper(stack, name));
        // each key the reference carries replaces the defined key whole (PS-578)
        field.putAll((Map<String, Object>) expandNode(overrides, definitions, stack));
        if (!(field.get("name") instanceof String) && !"skip".equals(field.get("type"))) {
            throw new SchemaException("the field resolved from '" + REF_PREFIX + name + "' has no `name`; "
                    + "the definition or the reference declares one (PS-578, PS-553)");
        }
        return new ArrayList<>(List.of(field));
    }

    private static Object lookupTable(String pointer, Map<String, Object> definitions, List<String> stack) {
        String name = refTarget(pointer);
        if (!definitionKind(definitions, name, stack).equals("lookup")) {
            throw new SchemaException("`lookup: " + pointer + "` names a definition that is not a "
                    + "lookup table (PS-579, PS-348)");
        }
        // a copy, keys stringified as every other mapping here is
        return expandNode(((Map<?, ?>) definitions.get(name)).get("lookup"), definitions, stack);
    }

    /**
     * PS-079, PS-391: a bytes format is one of four, and a separator applies to the two hex
     * ones. Any other format fell through to lowercase hex.
     */
    private static void checkBytesFormat(Map<String, Object> fm) {
        if (!"bytes".equals(fm.get("type"))) return;
        Object format = fm.getOrDefault("format", "hex");
        if (!List.of("hex", "hex:upper", "base64", "array").contains(format)) {
            throw new SchemaException("Field '" + fm.get("name") + "': bytes format " + format
                    + " is not one of hex, hex:upper, base64, array (PS-079)");
        }
        if (fm.containsKey("separator") && !"hex".equals(format) && !"hex:upper".equals(format)) {
            throw new SchemaException("Field '" + fm.get("name")
                    + "': `separator` applies only to the hex formats, not " + format + " (PS-391)");
        }
    }

    /** The whole-integer types a byte_group member may name (PS-070): u16 is u16[0:15]. */
    private static final Map<String, Integer> WHOLE_INTEGER_BYTES = Map.ofEntries(
            Map.entry("u8", 1), Map.entry("u16", 2), Map.entry("u24", 3), Map.entry("u32", 4),
            Map.entry("u64", 8), Map.entry("uint8", 1), Map.entry("uint16", 2),
            Map.entry("uint24", 3), Map.entry("uint32", 4), Map.entry("uint64", 8),
            Map.entry("s8", 1), Map.entry("s16", 2), Map.entry("s24", 3), Map.entry("s32", 4),
            Map.entry("s64", 8), Map.entry("i8", 1), Map.entry("i16", 2), Map.entry("i24", 3),
            Map.entry("i32", 4), Map.entry("i64", 8), Map.entry("int8", 1),
            Map.entry("int16", 2), Map.entry("int24", 3), Map.entry("int32", 4),
            Map.entry("int64", 8));

    /**
     * A byte_group member's {base bytes, low bit, high bit} (PS-070, CR-2026-131). A member
     * is a bit range; a whole integer, its full range; or a bool with {@code bit}, one bit
     * of a u8. Its bits are numbered in the group's assembled value (PS-364), so members of
     * any width compare directly. They were mapped most-significant-first within each
     * member's own base, which disagreed with decoding for members of different widths.
     */
    static int[] byteGroupMemberRange(Map<String, Object> member) {
        Object type = member.get("type");
        String typ = type == null ? "" : type.toString();
        Object name = member.get("name");
        Matcher m = BIT_RANGE.matcher(typ);
        if (m.matches()) {
            int lo = Integer.parseInt(m.group(3));
            int hi = Integer.parseInt(m.group(4));
            return new int[] {Math.max(1, Integer.parseInt(m.group(2)) / 8),
                    Math.min(lo, hi), Math.max(lo, hi)};
        }
        Integer whole = WHOLE_INTEGER_BYTES.get(typ);
        if (whole != null) return new int[] {whole, 0, whole * 8 - 1};
        if ("bool".equals(typ) && member.get("bit") instanceof Number bitNumber) {
            int bit = bitNumber.intValue();
            if (bit < 0 || bit > 7) {
                throw new SchemaException("byte_group member '" + name + "' is a bool at bit " + bit
                        + "; a bool member is one bit of a u8, 0 to 7 (PS-070)");
            }
            return new int[] {1, bit, bit};
        }
        throw new SchemaException("byte_group member '" + name
                + "' is not a bit range, a whole integer or a bool with `bit` (PS-364)");
    }

    /**
     * The bytes a byte_group covers (PS-070, CR-2026-131): with no {@code size}, its widest
     * member's base type, whichever bytes that member's bits fall in - a {@code u16[0:3]}
     * member makes it two bytes. A {@code size} may widen it; a narrower one is rejected at load
     * ({@link #checkByteGroup}). Decoding
     * read a group with no size as one byte, so a {@code u16[8:15]} member decoded from
     * the wrong byte, while encoding already widened to the base.
     */
    static int byteGroupWidth(List<Field> members, int declared) {
        int width = Math.max(1, declared);
        for (Field member : members == null ? List.<Field>of() : members) {
            FieldType type = member.getType();
            int base = type == FieldType.BITS ? Math.max(1, member.getBitBaseBytes())
                    : type != null && type.isInteger() ? type.defaultLength() : 1;
            width = Math.max(width, base);
        }
        return width;
    }

    /**
     * A byte_group's load-time rules: every member a kind PS-070 admits, no two members on
     * the same bits (PS-397), and a {@code size} no narrower than the widest member's base
     * (PS-070, CR-2026-131), which used to be widened silently.
     */
    @SuppressWarnings("unchecked")
    private static void checkByteGroup(Map<String, Object> fm) {
        Object group = fm.get("byte_group");
        List<?> members = group instanceof List<?> list ? list
                : group instanceof Map<?, ?> map && map.get("fields") instanceof List<?> fl ? fl
                : List.of();
        Object size = group instanceof Map<?, ?> map ? map.get("size") : fm.get("size");
        List<Map.Entry<Object, int[]>> seen = new ArrayList<>();
        int widest = 1;
        for (Object raw : members) {
            if (!(raw instanceof Map)) continue;
            Map<String, Object> member = (Map<String, Object>) raw;
            int[] range = byteGroupMemberRange(member);
            for (Map.Entry<Object, int[]> other : seen) {
                if (range[1] <= other.getValue()[2] && other.getValue()[1] <= range[2]) {
                    throw new SchemaException("byte_group members '" + other.getKey() + "' and '"
                            + member.get("name") + "' overlap (PS-397)");
                }
            }
            seen.add(Map.entry(String.valueOf(member.get("name")), range));
            widest = Math.max(widest, range[0]);
        }
        if (size instanceof Number declared && declared.intValue() < widest) {
            throw new SchemaException("byte_group declares size " + declared.intValue()
                    + ", narrower than its widest member's " + widest + " bytes (PS-070)");
        }
    }

    /**
     * PS-018: a port key is 1 to 255 (CR-2026-041). PS-335, PS-337: a top-level fPort is
     * such a port, and beside `ports` every key equals it (CR-2026-042). The top-level key
     * selects nothing (PS-336) and is not read again.
     */
    private static void checkPortDeclarations(Map<String, Object> raw) {
        Map<?, ?> ports = raw.get("ports") instanceof Map<?, ?> m ? m : Map.of();
        for (Object key : ports.keySet()) {
            String k = String.valueOf(key);
            if (k.equals("default")) continue;
            int n;
            try { n = Integer.parseInt(k); } catch (NumberFormatException e) { n = -1; }
            if (n < 1 || n > 255) {
                throw new SchemaException("ports." + k
                        + ": a port key must be an integer from 1 to 255 or default (PS-018)");
            }
        }
        String name = raw.containsKey("fPort") ? "fPort" : "fport";
        if (!raw.containsKey(name)) return;
        Object declared = raw.get(name);
        if (!(declared instanceof Integer port) || port < 1 || port > 255) {
            throw new SchemaException("top-level " + name
                    + " must be an integer from 1 to 255, got " + declared + " (PS-335)");
        }
        for (Object key : ports.keySet()) {
            if (!String.valueOf(key).equals(String.valueOf(port))) {
                throw new SchemaException("top-level " + name + " is " + port + " but ports also declares "
                        + key + "; every ports key must equal it (PS-337)");
            }
        }
    }

    /** Bits of a base value, sign-extended from the range's width for an sN base (PS-353). */
    static long extractRange(long base, boolean signed, int start, int bits) {
        long mask = bits >= 64 ? -1L : (1L << bits) - 1;
        long value = (base >>> start) & mask;
        if (signed && bits < 64 && value >= (1L << (bits - 1))) value -= 1L << bits;
        return value;
    }

    /** The values of a quoted list case key, "[1, 2, 0x10]" (PS-398), or null. */
    static List<Long> parseListCaseKey(String text) {
        text = text.trim();
        if (!text.startsWith("[") || !text.endsWith("]")) return null;
        List<Long> values = new ArrayList<>();
        for (String raw : text.substring(1, text.length() - 1).split(",")) {
            String part = raw.trim();
            if (part.isEmpty()) continue;
            try {
                values.add(part.toLowerCase().startsWith("0x")
                        ? Long.parseLong(part.substring(2), 16) : Long.parseLong(part));
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return values;
    }

    /** The bytes one element always takes, or 0 where it varies (PS-344a). */
    /**
     * The bytes one tag component takes: its {@code length} where declared, else its type's
     * width. This read {@code length} defaulting to 1, so a u16 component was read as one
     * byte where Python and the generated codec take two.
     */
    static int tagFieldWidth(Field tf) {
        if (tf.getLength() > 0) return tf.getLength();
        FieldType t = tf.getType();
        if (t != null && t.isInteger() && t.defaultLength() > 0) return t.defaultLength();
        return 1;
    }

    private static int fixedElementSize(List<Field> fields) {
        int total = 0;
        for (Field f : fields) {
            FieldType t = f.getType();
            if (t == FieldType.NUMBER || t == FieldType.BOOLEAN
                    || (t == FieldType.STRING && f.getValue() != null)) continue;
            if (t == FieldType.BITS || t == FieldType.BOOL) { total += f.getConsume(); continue; }
            if (f.isEcho()) continue;               // PS-588: it advances nothing
            if (t == FieldType.BYTES || t == FieldType.ASCII || t == FieldType.HEX
                    || t == FieldType.BASE64 || t == FieldType.SKIP) {
                if (f.getLength() <= 0) return 0;
                total += f.getLength();
                continue;
            }
            if (t.isInteger() || t.isFloat() || t == FieldType.UDEC || t == FieldType.SDEC) {
                total += t == FieldType.UDEC || t == FieldType.SDEC ? 1
                        : t.isFloat() ? (t == FieldType.F16 || t == FieldType.BF16 ? 2 : t == FieldType.F32 ? 4 : 8)
                        : t.defaultLength();
                continue;
            }
            return 0;
        }
        return total;
    }

    /** The PS-344 error: the repeat, reported as a ragged tail. */
    private static SchemaException.DecodeException raggedTailError(Field field, int remaining,
            int elementSize, int offset) {
        String need = elementSize > 0 ? ", fewer than the " + elementSize + " an element takes" : "";
        return new SchemaException.DecodeException("repeat '" + field.getName() + "' ends in a ragged tail: "
                + remaining + " byte(s) at offset " + offset + need + " (PS-343)");
    }

    private static final List<String> UNSIGNED_TYPES = List.of("u8", "u16", "u24", "u32", "u64",
            "uint8", "uint16", "uint24", "uint32", "uint64");

    /**
     * PS-426: match_value is withdrawn. PS-422: `encoding` is a named code on a uN.
     * PS-430: a bitfield_string part is decimal, sdecimal, hex or hex:upper, with an optional :N. PS-364: a byte_group
     * member declares no endian.
     */
    @SuppressWarnings("unchecked")
    private static void checkWave4(Map<String, Object> fm) {
        String at = "Field '" + fm.get("name") + "': ";
        Numeral.check(fm);                                    // PS-560
        if (fm.containsKey("match_value")) {
            throw new SchemaException(at + "match_value is withdrawn; write a signed type (sN), a "
                    + "signed bit range (sN[start:end]), encoding, match or guard instead (PS-426)");
        }
        if (fm.containsKey("encoding")) {
            Object encoding = fm.get("encoding");
            if (!List.of("sign_magnitude", "bcd", "gray").contains(encoding)) {
                throw new SchemaException(at + "encoding " + encoding
                        + " is not one of sign_magnitude, bcd, gray (PS-422)");
            }
            if (!UNSIGNED_TYPES.contains(fm.get("type"))) {
                throw new SchemaException(at + "encoding applies only to an unsigned integer type uN, not "
                        + fm.get("type") + " (PS-422)");
            }
        }
        if ("bitfield_string".equals(fm.get("type")) && fm.get("parts") instanceof List<?> parts) {
            for (Object raw : parts) {
                // PS-430 (CR-2026-170): the spec prototype's text, unprefixed.
                if (raw instanceof List<?> part && part.size() > 2) BitfieldParts.parse(part.get(2));
            }
        }
        Object group = fm.get("byte_group");
        List<?> members = group instanceof List<?> l ? l
                : group instanceof Map<?, ?> m && m.get("fields") instanceof List<?> fl ? fl : List.of();
        for (Object member : members) {
            if (member instanceof Map<?, ?> mm && mm.containsKey("endian")) {
                throw new SchemaException("byte_group member '" + mm.get("name")
                        + "' declares endian; the group's byte order is declared on the group (PS-364)");
            }
        }
    }

    /**
     * PS-510 (CR-2026-102): a field's {@code ipso} is a mapping carrying {@code object}, and
     * its {@code senml} a mapping. {@code ipso: 3303} loaded and was dropped, so the author
     * believed the field annotated and no consumer saw an annotation. The wording is the
     * spec prototype's, and Python, Go and C# give it identically. A {@code semantic} is a
     * string, a TTN normalized path (PS-470, CR-2026-179).
     */
    static void checkAnnotations(Map<?, ?> fm) {
        Object name = fm.get("name") instanceof String s ? s : "?";
        if (fm.containsKey("ipso")
                && !(fm.get("ipso") instanceof Map<?, ?> ipso && ipso.containsKey("object"))) {
            throw new SchemaException("The ipso of '" + name + "' is a mapping that carries `object`, "
                    + "such as {object: 3303}; a bare object number is not a shorthand (PS-510)");
        }
        if (fm.containsKey("senml") && !(fm.get("senml") instanceof Map)) {
            throw new SchemaException("The senml of '" + name + "' is a mapping, such as {unit: Cel} (PS-510)");
        }
        if (fm.containsKey("semantic") && !(fm.get("semantic") instanceof String)) {
            // PS-470 (CR-2026-179): a TTN normalized path, so a string.
            throw new SchemaException("The semantic of '" + name + "' is a string naming a normalized "
                    + "measurement type, such as air.temperature (PS-470)");
        }
    }

    /**
     * checkAnnotations over every field mapping of the {@code definitions}, referenced or
     * not, as Go and C# parse them; a referenced one is also checked where it is spliced.
     */
    private static void checkDefinitionAnnotations(Object node) {
        if (node instanceof Map<?, ?> m) {
            checkAnnotations(m);
            for (Map.Entry<?, ?> e : m.entrySet()) {
                Object key = e.getKey();
                if ("ipso".equals(key) || "senml".equals(key) || "semantic".equals(key)) continue;
                checkDefinitionAnnotations(e.getValue());
            }
        } else if (node instanceof List<?> list) {
            for (Object item : list) checkDefinitionAnnotations(item);
        }
    }

    /** PS-358: a literal's value matches its type, and it carries no arithmetic. */
    private static void checkLiteral(Map<String, Object> fm) {
        Object type = fm.get("type");
        String at = "Field '" + fm.get("name") + "': ";
        // PS-519: `boolean` is a literal type only, so it declares `value`. PS-520: the
        // value is a boolean; "true", 1 and null are refused. SnakeYAML reads YAML 1.1,
        // so `yes` arrives as true, which PS-520 does not ask a decoder to detect.
        if ("boolean".equals(type)) {
            if (!fm.containsKey("value")) {
                throw new SchemaException(at + "type boolean is a literal and declares no value (PS-519)");
            }
            if (!(fm.get("value") instanceof Boolean)) {
                throw new SchemaException(at + "a boolean literal's value must be true or false, not "
                        + fm.get("value") + " (PS-520)");
            }
        }
        if (!fm.containsKey("value")
                || !("string".equals(type) || "number".equals(type) || "boolean".equals(type))) return;
        Object value = fm.get("value");
        if ("string".equals(type) && !(value instanceof String)) {
            throw new SchemaException(at + "a string literal's value must be a string (PS-358)");
        }
        if ("number".equals(type) && !(value instanceof Number)) {
            throw new SchemaException(at + "a number literal's value must be a number (PS-358)");
        }
        for (String key : List.of("ref", "polynomial", "compute", "lookup", "transform", "mult", "div", "add")) {
            if (fm.containsKey(key)) {
                throw new SchemaException(at + "a literal must not declare " + key + " (PS-358)");
            }
        }
    }

    /** The PS-396 error: the repeat, its limit and the payload left unparsed. */
    private static SchemaException.DecodeException repeatLimitError(Field field, int limit,
            String mode, int offset, int end) {
        return new SchemaException.DecodeException(String.format(
                "repeat '%s' exceeds its max of %d element(s) (%s); %d byte(s) at offset %d left unparsed (PS-396)",
                field.getName(), limit, mode, end - offset, offset));
    }

    /** The stages PS-098 and the PS-115 table define, by name; {@code op} names one instead. */
    private static final List<String> TRANSFORM_OPERATIONS = List.of("add", "mult", "div",
            "sqrt", "abs", "pow", "log10", "log", "floor", "ceiling", "op");
    /** Parameters of an {@code op:} stage, part of its one operation (PS-452). */
    private static final List<String> TRANSFORM_OP_PARAMETERS = List.of("decimals", "ties");

    /**
     * CR-2026-071/073, on list members, so a construct body parsed as a synthetic field is
     * not mistaken for a field read from the payload. Mirrors arithmetic_errors in
     * tools/schema_interpreter.py.
     *
     * <p>PS-445: a guard belongs to a computed field. On a field read from the payload, or
     * a literal, it was ignored with success - {@code guard} on a {@code u8} decoded every
     * value - so a schema meaning "no reading below this" reported them all.
     */
    private static void checkArithmetic(Map<String, Object> fm) {
        if (fm.containsKey("guard")) {
            Object type = fm.get("type");
            boolean computed = ("number".equals(type) || "integer".equals(type))
                    && (fm.get("ref") != null || fm.get("compute") != null);
            if (!computed) {
                throw new SchemaException("Field '" + fm.get("name") + "': a guard is declared "
                        + "only on a computed field (ref or compute); for a value read from the "
                        + "payload use sentinel or out_of_range: omit (PS-445)");
            }
        }
        if (fm.containsKey("transform") && !(fm.get("transform") instanceof List)) {
            throw new SchemaException("Field '" + fm.get("name")
                    + "': `transform` must be a list of stages (PS-102)");
        }
    }

    /**
     * PS-390: a stage naming an operation outside the PS-115 table is rejected, not
     * skipped. {@code {round: n}} was accepted and did nothing, so the field was reported
     * unrounded with success; {@code round} is the {@code op:} form only.
     *
     * <p>PS-452: a stage holds exactly one operation. {@code {add: 1, mult: 2}} decoded
     * here as mult-then-add (1 to 3) where Python gave 7 and the generated codec 4;
     * {@code {op: round, add: 1}} rounded and dropped the add. A stage with none, such as
     * {@code {decimals: 2}}, was rejected but as PS-390.
     */
    private static void checkStage(Object raw, Object fieldName) {
        if (!(raw instanceof Map<?, ?> rawStage)) {
            // Skipped before, so a malformed stage applied nothing with success.
            throw new SchemaException("Field '" + fieldName + "' transform stage " + raw
                    + " is not a mapping holding one operation (PS-452)");
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> stage = (Map<String, Object>) rawStage;
        String at = "Field '" + fieldName + "' transform stage " + stage + ": ";
        if (stage.containsKey("clamp")) {
            throw new SchemaException(Cr159.CLAMP);     // CR-2026-159
        }
        if (stage.containsKey("round")) {
            throw new SchemaException(at + "`{round: n}` is not a transform stage; write "
                    + "{op: round, decimals: n} (PS-390)");
        }
        List<String> unknown = new ArrayList<>();
        List<String> operations = new ArrayList<>();
        for (Object rawKey : rawStage.keySet()) {
            String key = String.valueOf(rawKey);
            if (TRANSFORM_OPERATIONS.contains(key)) operations.add(key);
            else if (!TRANSFORM_OP_PARAMETERS.contains(key)) unknown.add(key);
        }
        if (!unknown.isEmpty()) {
            throw new SchemaException(at + "names no operation of the PS-115 table: "
                    + String.join(", ", unknown) + " (PS-390)");
        }
        if (operations.size() != 1) {
            throw new SchemaException(at + "must hold exactly one operation, and holds "
                    + (operations.isEmpty() ? "none" : String.join(", ", operations)) + " (PS-452)");
        }
        if (!stage.containsKey("op") && TRANSFORM_OP_PARAMETERS.stream().anyMatch(stage::containsKey)) {
            throw new SchemaException(at + "`decimals` and `ties` belong to an `op:` stage (PS-452)");
        }
        if (stage.containsKey("op")) {
            if (!"round".equals(stage.get("op"))) {
                throw new SchemaException(at + "names an unknown operation (PS-390)");
            }
            Object ties = stage.get("ties");
            if (ties != null && !"even".equals(ties) && !"away".equals(ties)
                    && !"up".equals(ties)) {
                throw new SchemaException(at + "round `ties` must be even, away or up (PS-390)");
            }
        }
    }

    /** The keys that make a field a construct, so it declares no type of its own. */
    private static final List<String> CONSTRUCT_KEYS =
            List.of("$ref", "flagged", "tlv", "byte_group", "match", "records");

    private static boolean hasConstruct(Map<String, Object> fm) {
        for (String key : CONSTRUCT_KEYS) {
            if (fm.containsKey(key)) return true;
        }
        return false;
    }

    @SuppressWarnings("unchecked")
    private static Field parseField(Map<String, Object> fm) {
        Field f = new Field();
        f.setRaw(fm);
        
        f.setName((String) fm.get("name"));
        String rawType = (String) fm.get("type");
        // The bracket form must be recognised before FieldType.fromString, which cannot
        // parse it and now rejects a spelling it does not know rather than returning U8.
        Matcher bitRange = BIT_RANGE.matcher(rawType == null ? "" : rawType.trim());
        boolean isBitRange = bitRange.matches();
        try {
            f.setType(isBitRange ? FieldType.BITS : FieldType.fromString(rawType));
        } catch (SchemaException e) {
            // PS-328: the rejection names the field as well as the spelling.
            throw new SchemaException("Field '" + fm.get("name") + "': " + e.getMessage());
        }
        // `integer` is `number` declaring an integer result (PS-283).
        if (f.getType() == FieldType.INTEGER) {
            f.setType(FieldType.NUMBER);
            f.setIntegerResult(true);
        }
        // `length: remaining` (PS-014) is carried as a negative sentinel; toInt would
        // otherwise silently return the 0 default and the field would read one byte.
        Object lengthSpec = fm.get("length");
        if (lengthSpec instanceof String s && s.trim().equalsIgnoreCase("remaining")) {
            f.setLength(-1);
        } else if (Wave6b.lengthRef(lengthSpec) != null) {
            // PS-464: the name of a preceding field, with or without `$`, whose value is
            // the count. toInt read it as 0, so the type's default length was read.
            f.setLengthRef(Wave6b.lengthRef(lengthSpec));
        } else {
            f.setLength(toInt(lengthSpec, 0));
            f.setLengthDeclared(lengthSpec instanceof Number);
        }
        // Wave 6b: optional (PS-402), sentinel (PS-427), out_of_range: omit (PS-428).
        f.setOptional(Boolean.TRUE.equals(fm.get("optional")));
        if (fm.get("sentinel") instanceof List<?> sentinels) {
            List<Long> parsed = new ArrayList<>();
            for (Object s : sentinels) {
                if (s instanceof Number n) parsed.add(n.longValue());
            }
            f.setSentinel(parsed);
        }
        if ("omit".equals(fm.get("out_of_range")) && fm.get("valid_range") instanceof List<?> bounds
                && bounds.size() == 2 && bounds.get(0) instanceof Number lo && bounds.get(1) instanceof Number hi) {
            f.setOmitOutside(new double[] {lo.doubleValue(), hi.doubleValue()});
        }
        if (fm.get("valid_range") instanceof List<?> range && range.size() == 2
                && range.get(0) instanceof Number lo && range.get(1) instanceof Number hi) {
            f.setValidRange(new double[] {lo.doubleValue(), hi.doubleValue()});
        }
        f.setByteOffset(toInt(fm.get("byte_offset"), 0));
        f.setBitOffset(toInt(fm.get("bit_offset"), 0));
        if (fm.get("bit") != null) {
            f.setBoolBit(toInt(fm.get("bit"), 0));
        }
        f.setBits(toInt(fm.get("bits"), 0));
        f.setConsume(toInt(fm.get("consume"), 0));
        f.setConsumeDeclared(fm.get("consume") instanceof Number);
        f.setEndian((String) fm.get("endian"));

        // A `u8[lo:hi]` range is a bit field. FieldType.fromString does not recognise
        // the bracket form and fell through to U8, so the whole byte was read instead
        // of the bits: a packed flag byte reported its raw value, which is why
        // em310-tilt's threshold_x decoded as 17 rather than "trigger". The bit offset
        // and width are applied here rather than above so they override any explicit
        // `bit_offset:`/`bits:` keys parsed in between.
        if (isBitRange) {
            int start = Integer.parseInt(bitRange.group(3));
            int end = Integer.parseInt(bitRange.group(4));
            f.setSignedBits("s".equals(bitRange.group(1)));
            f.setBitOffset(start);
            f.setBits(end - start + 1);
            // The base width is part of the type: u24[4:23] takes bits 4-23 of a
            // 24-bit big-endian value, so all three bytes are read before masking.
            f.setBitBaseBytes(Math.max(1, Integer.parseInt(bitRange.group(2)) / 8));
            f.setType(FieldType.BITS);
        }
        
        // Modifiers
        if (fm.containsKey("mult")) {
            f.setMult(toDouble(fm.get("mult")));
        }
        if (fm.containsKey("div")) {
            f.setDiv(toDouble(fm.get("div")));
        }
        if (fm.containsKey("add")) {
            f.setAdd(toDouble(fm.get("add")));
        }
        
        // Modifier key order is deliberately not tracked: the canonical order
        // (mult, div, add) applies however the source was written (PS-101).

        f.setVar((String) fm.get("var"));
        f.setValue(fm.get("value"));

        // Enumeration type: base integer plus an integer-to-name mapping (PS-067),
        // with `default` naming what an unmapped value reports (PS-068). None of
        // this was parsed, so an `enum` field fell through to u8 and reported the
        // raw number.
        f.setBase((String) fm.get("base"));
        if (fm.get("values") instanceof Map<?, ?> valuesRaw) {
            Map<Integer, String> values = new HashMap<>();
            for (Map.Entry<?, ?> entry : valuesRaw.entrySet()) {
                Object label = entry.getValue();
                // A value may be a plain name or a {name, description} mapping.
                if (label instanceof Map<?, ?> described) {
                    label = described.get("name");
                }
                values.put(toInt(entry.getKey(), 0), String.valueOf(label));
            }
            f.setValues(values);
            if (fm.get("default") != null) {
                f.setEnumDefault(String.valueOf(fm.get("default")));
            }
        } else if (fm.get("values") instanceof List<?> valuesList) {
            // The sequence form names codes from zero; it was not parsed, so every code
            // of such an enum read as unmapped.
            Map<Integer, String> values = new HashMap<>();
            for (int i = 0; i < valuesList.size(); i++) {
                Object label = valuesList.get(i);
                if (label instanceof Map<?, ?> described) {
                    label = described.get("name");
                }
                values.put(i, String.valueOf(label));
            }
            f.setValues(values);
            if (fm.get("default") != null) {
                f.setEnumDefault(String.valueOf(fm.get("default")));
            }
        }

        // Computed fields (type: number). None of this was parsed, so every schema
        // deriving a value from an earlier field reported nothing for it.
        if (fm.get("ref") != null) {
            f.setRef(String.valueOf(fm.get("ref")));
        }
        if (fm.get("polynomial") instanceof List<?> coefficients) {
            List<Double> parsed = new ArrayList<>();
            for (Object coefficient : coefficients) {
                parsed.add(toDouble(coefficient));
            }
            f.setPolynomial(parsed);
        }
        if (fm.get("compute") instanceof Map<?, ?> computeRaw) {
            Field.Compute compute = new Field.Compute();
            if (computeRaw.get("op") != null) compute.setOp(String.valueOf(computeRaw.get("op")));
            compute.setA(computeRaw.get("a"));
            compute.setB(computeRaw.get("b"));
            f.setCompute(compute);
        }
        if (fm.get("guard") instanceof Map<?, ?> guardRaw) {
            f.setGuard(parseGuard(guardRaw));
        }
        
        // Transform array
        Object transformRaw = fm.get("transform");
        if (transformRaw instanceof List) {
            List<Field.Transform> transforms = new ArrayList<>();
            for (Object tr : (List<?>) transformRaw) {
                checkStage(tr, fm.get("name"));
                if (tr instanceof Map) {
                    Map<String, Object> tm = (Map<String, Object>) tr;
                    Field.Transform t = new Field.Transform();
                    if (tm.containsKey("add")) t.setAdd(toDouble(tm.get("add")));
                    if (tm.containsKey("mult")) t.setMult(toDouble(tm.get("mult")));
                    if (tm.containsKey("div")) t.setDiv(toDouble(tm.get("div")));
                    // {op: round, decimals: N}. Unparsed until now, so a schema
                    // rounding its output reported the unrounded value instead.
                    if (tm.containsKey("op")) t.setOp(String.valueOf(tm.get("op")));
                    if (tm.containsKey("decimals")) t.setDecimals(toInt(tm.get("decimals"), 0));
                    // Unary maths stages. dl-blg's thermistor needs a natural log
                    // and a cube, and had no way to say so in this binding.
                    if (tm.containsKey("sqrt")) t.setSqrt(toBoolean(tm.get("sqrt")));
                    if (tm.containsKey("abs")) t.setAbs(toBoolean(tm.get("abs")));
                    if (tm.containsKey("log10")) t.setLog10(toBoolean(tm.get("log10")));
                    if (tm.containsKey("log")) t.setLog(toBoolean(tm.get("log")));
                    if (tm.containsKey("pow")) t.setPow(toDouble(tm.get("pow")));
                    if (tm.containsKey("floor")) t.setFloor(toDouble(tm.get("floor")));
                    if (tm.containsKey("ceiling")) t.setCeiling(toDouble(tm.get("ceiling")));
                    if (tm.containsKey("ties")) t.setTies(String.valueOf(tm.get("ties")));
                    transforms.add(t);
                }
            }
            f.setTransform(transforms);
        }
        
        // Lookup table
        Object lookupRaw = fm.get("lookup");
        if (lookupRaw instanceof Map) {
            // A label keeps the type the schema gave it: a string, a number or a boolean
            // (PS-106). String.valueOf made every one text, so a boolean label was
            // reported as the string "true" (CR-2026-104).
            Map<Integer, Object> lookup = new HashMap<>();
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) lookupRaw).entrySet()) {
                if ("default".equals(String.valueOf(entry.getKey()))) {
                    f.setLookupDefault(lookupLabel(entry.getValue()));
                    continue;
                }
                int key = toInt(entry.getKey(), 0);
                lookup.put(key, lookupLabel(entry.getValue()));
            }
            f.setLookup(lookup);
        } else if (lookupRaw instanceof List) {
            // Sequence form, indexed from zero (PS-104). This was unparsed, so a
            // schema using it decoded a raw integer instead of its label.
            List<?> items = (List<?>) lookupRaw;
            Map<Integer, Object> lookup = new HashMap<>();
            for (int i = 0; i < items.size(); i++) {
                lookup.put(i, lookupLabel(items.get(i)));
            }
            f.setLookup(lookup);
            f.setLookupSequence(true);
        }
        Object nameFromRaw = fm.get("name_from");
        if (nameFromRaw != null) {
            f.setNameFrom(String.valueOf(nameFromRaw));
        }
        
        // Nested fields
        Object fieldsRaw = fm.get("fields");
        if (fieldsRaw instanceof List) {
            f.setFields(parseFields((List<Map<String, Object>>) fieldsRaw));
        } else if ("repeat".equals(fm.get("type")) && fm.get("element") instanceof Map<?, ?> element) {
            // PS-528: a repeat of single values decodes its element as a one-field record
            // under the element's name, or "element", and reports the value. Wave6a has
            // already rejected both, neither, and a non-mapping element.
            // PS-545: an element named with `_` is decoded under "element", with `var`
            // keeping `$_x` bound, so its value is reported.
            Map<String, Object> member = Wave6a.elementMember(element);
            String elementName = (String) member.get("name");
            List<Map<String, Object>> one = new ArrayList<>();
            one.add(member);
            f.setFields(parseFields(one));
            f.setElementName(elementName);
        }
        if (fm.get("index_base") instanceof Number base) f.setIndexBase(base.longValue());  // PS-530

        Object casesRaw = fm.get("cases");

        // TLV cases (map format). Parsed whenever `cases` appears alongside anything
        // that marks the block as TLV, not only when the type is already TLV: an
        // inline `- tlv: {...}` block is parsed here before its caller sets the type,
        // so requiring the type first left tlvCases null and every TLV schema decoded
        // to an empty result with no error. `tag_size` counts as such a marker - a
        // block with a simple one-byte tag has neither tag_fields nor tag_key, which
        // is why elsys/ers decoded to nothing.
        if ((f.getType() == FieldType.TLV || fm.containsKey("tag_fields")
                || fm.containsKey("tag_key") || fm.containsKey("tag_size"))
                && casesRaw instanceof Map) {
            parseTlvCases(f, fm);
        }
        
        // Repeat fields
        f.setCount(fm.get("count"));
        f.setByteLength(fm.get("byte_length"));
        f.setUntil((String) fm.get("until"));
        f.setMax(toInt(fm.get("max"), 0));
        f.setMin(toInt(fm.get("min"), 0));
        // The iterator and reserved trailers (wave 6a). Their schema rules are checked by
        // Wave6a before parsing; `reserve` is read here for a repeat and, through the
        // parse of the inline block, for a tlv (PS-471).
        if (fm.get("index") instanceof String index && !index.isEmpty()) f.setIndex(index);
        if (fm.get("count_as") instanceof String countAs && !countAs.isEmpty()) f.setCountAs(countAs);
        if (fm.get("present_if") instanceof Map<?, ?> presentIf) {
            f.setPresentIf(parseGuard(Map.of("when", List.of(presentIf))));
        }
        if (fm.containsKey("carry")) f.setCarry(fm.get("carry"));
        f.setReserve(toInt(fm.get("reserve"), 0));
        if (fm.get("trailer") instanceof List<?> trailer) {
            f.setTrailer(parseFields((List<Map<String, Object>>) trailer));
        }

        // Bytes format
        f.setFormat((String) fm.get("format"));
        f.setSeparator((String) fm.get("separator"));
        
        // TLV fields
        f.setTagSize(toInt(fm.get("tag_size"), 0));
        f.setLengthSize(toInt(fm.get("length_size"), 0));
        Object tagFieldsRaw = fm.get("tag_fields");
        if (tagFieldsRaw instanceof List) {
            f.setTagFields(parseFields((List<Map<String, Object>>) tagFieldsRaw));
        }
        f.setTagKey(fm.get("tag_key"));
        if (fm.containsKey("merge")) {
            f.setMerge((Boolean) fm.get("merge"));
        }
        f.setUnknown((String) fm.get("unknown"));
        
        // Bitfield string
        f.setDelimiter((String) fm.get("delimiter"));
        f.setPrefix((String) fm.get("prefix"));
        Object partsRaw = fm.get("parts");
        if (partsRaw instanceof List) {
            List<List<Object>> parts = new ArrayList<>();
            for (Object p : (List<?>) partsRaw) {
                if (p instanceof List) {
                    parts.add(new ArrayList<>((List<?>) p));
                }
            }
            f.setParts(parts);
        }
        
        if (fm.get("encoding") instanceof String encoding) f.setEncoding(encoding);
        if (fm.get("numeral") instanceof String numeral) f.setNumeral(numeral);
        if (fm.get("checksum") instanceof Map<?, ?> checksum) {                 // PS-585
            f.setChecksum((Map<String, Object>) checksum, String.valueOf(fm.get("type")));
        }

        // byte_group: fields packed into shared bytes. Written either as a list of
        // fields with a sibling `size`, or as {size: N, fields: [...]}.
        Object byteGroupRaw = fm.get("byte_group");
        if (byteGroupRaw instanceof Map<?, ?> groupMap) {
            if (groupMap.get("fields") instanceof List<?> groupFields) {
                f.setByteGroup(parseFields((List<Map<String, Object>>) groupFields));
            }
            f.setByteGroupSize(byteGroupWidth(f.getByteGroup(), toInt(groupMap.get("size"), 1)));
            if (groupMap.get("endian") instanceof String groupEndian) f.setGroupEndian(groupEndian);
        } else if (byteGroupRaw instanceof List<?> groupFields) {
            f.setByteGroup(parseFields((List<Map<String, Object>>) groupFields));
            f.setByteGroupSize(byteGroupWidth(f.getByteGroup(), toInt(fm.get("size"), 1)));
        }

        // Flagged construct
        Object flaggedRaw = fm.get("flagged");
        if (flaggedRaw instanceof Map) {
            Map<String, Object> flaggedMap = (Map<String, Object>) flaggedRaw;
            Field.FlaggedDef fd = new Field.FlaggedDef();
            fd.setField((String) flaggedMap.get("field"));
            fd.setRaw(flaggedMap);
            
            Object groupsRaw = flaggedMap.get("groups");
            if (groupsRaw instanceof List) {
                List<Field.FlaggedGroup> groups = new ArrayList<>();
                for (Object gr : (List<?>) groupsRaw) {
                    if (gr instanceof Map) {
                        Map<String, Object> gm = (Map<String, Object>) gr;
                        Field.FlaggedGroup g = new Field.FlaggedGroup();
                        g.setBit(toInt(gm.get("bit"), 0));
                        Object gFieldsRaw = gm.get("fields");
                        if (gFieldsRaw instanceof List) {
                            g.setFields(parseFields((List<Map<String, Object>>) gFieldsRaw));
                        }
                        groups.add(g);
                    }
                }
                fd.setGroups(groups);
            }
            f.setFlagged(fd);
        }
        
        // Match inline: `- match: {field: $evt, cases: {0x00: [...], ...}}`. This form
        // was not parsed at all, so a schema written with it decoded only the fields
        // ahead of the block and reported nothing for any case - the whole of
        // radio-bridge/rbs30x past its header.
        Object matchRaw = fm.get("match");
        if (matchRaw instanceof Map<?, ?> matchMap) {
            Field matchField = new Field();
            matchField.setType(FieldType.MATCH);
            matchField.setRaw((Map<String, Object>) matchMap);       // located by _decode (PS-572)
            Object on = matchMap.get("field");
            if (on instanceof String) {
                matchField.setOn((String) on);
            }
            // The block's own `name` and `length`: a match with no `field:` reads its
            // discriminator from the payload, and both the decoder's read width and the
            // encoder's write width come from `length`. Dropping them left a two-byte
            // discriminator read as one byte.
            if (matchMap.get("name") instanceof String matchName) {
                matchField.setName(matchName);
            }
            if (matchMap.get("length") != null) {
                matchField.setLength(toInt(matchMap.get("length"), 0));
            }
            // PS-414: the discriminator is the bytes remaining, and nothing is read.
            matchField.setMatchRemaining(Boolean.TRUE.equals(matchMap.get("remaining")));
            if (matchMap.get("var") instanceof String matchVar) {
                matchField.setVar(matchVar);
            }
            if (matchMap.containsKey("default")) {
                // Parsed here rather than at decode time so the encoder can use it too:
                // it is handed already-parsed Fields and has no parseFields of its own
                // (CR-2026-027).
                Object declared = matchMap.get("default");
                if (declared instanceof List<?> declaredFields) {
                    matchField.setMatchDefault(
                            parseFields((List<Map<String, Object>>) declaredFields));
                } else {
                    matchField.setMatchDefault(declared);
                }
            }
            matchField.setCases(parseCaseMap(matchMap.get("cases")));
            f.setMatchInline(matchField);
        }

        // PS-541: field lists, each yielding one record of the enclosing repeat element.
        // Where it may appear and its shape are Wave6a.recordsErrors', checked at load.
        if (fm.get("records") instanceof List<?> bodies && fm.get("type") == null) {
            List<List<Field>> parsed = new ArrayList<>();
            for (Object body : bodies) {
                if (body instanceof List<?> list) {
                    parsed.add(parseFields((List<Map<String, Object>>) list));
                }
            }
            f.setRecords(parsed);
        }

        // TLV inline
        Object tlvRaw = fm.get("tlv");
        if (tlvRaw instanceof Map) {
            Field tlvField = parseField((Map<String, Object>) tlvRaw);
            tlvField.setType(FieldType.TLV);
            // A block with a one-byte tag need declare none of tag_size, tag_fields and
            // tag_key, and then nothing above marked it as a tlv, so its cases were never
            // parsed and every tag was unknown.
            if (tlvField.getTlvCases() == null) parseTlvCases(tlvField, (Map<String, Object>) tlvRaw);
            f.setTlvInline(tlvField);
        }
        
        return f;
    }

    /**
     * The interpreter output (PS-174, PS-175, PS-180): the decoded map with {@code _meta}
     * added as its last key. {@link #decode} and {@link #decodeWithPort} are unchanged;
     * a generated codec never carries {@code _meta} (PS-467).
     *
     * <p>The input context (PS-495) is the FPort - selecting the port entry exactly as
     * {@link #decodeWithPort} does, or as {@link #decode} where it is null - and the
     * device's {@code recvTime} and {@code devEUI}. {@code _meta} holds {@code schema},
     * {@code version} where the document declares one, {@code device_eui} (PS-496),
     * {@code rx_time} (PS-177) and {@code fPort} as supplied (PS-340 to PS-342), each
     * omitted where its source is absent, then {@code fields}: one entry per top-level
     * reported key (PS-181), from the declaration that produced it (PS-490, PS-492).
     *
     * <p>A failed decode throws exactly as the decode does, so there is no {@code _meta}
     * (PS-497); a malformed devEUI or recvTime throws too.
     */
    public Map<String, Object> interpret(byte[] data, InputContext in) {
        return interpret(data, in, false);
    }

    /**
     * {@link #interpret(byte[], InputContext)}, and with {@code decodeRecord} the output
     * also carries {@code _decode} after {@code _meta} (PS-572, CR-2026-162): the location
     * of every match evaluated with the case it took and the discriminator it read, every
     * internal field decoded with its value, and every flagged construct with its flags.
     * {@link Encoder} follows it where the input is given back with it (PS-573).
     */
    public Map<String, Object> interpret(byte[] data, InputContext in, boolean decodeRecord) {
        InputContext context = in != null ? in : new InputContext(null, null, null);
        Integer fPort = context.fPort();
        DecodeContext ctx = new DecodeContext(data, endian);
        ctx.setRepeatOnlyNames(repeatOnlyNames);
        if (decodeRecord) ctx.setRecord(new DecodeRecord(pointers, null));
        List<Field> selected;
        if (fPort == null) {
            if (ports != null && !ports.isEmpty()) {
                throw new SchemaException.DecodeException("no FPort was supplied, and schema '"
                        + name + "' selects its fields by port (PS-459)");
            }
            selected = fields;
        } else {
            selected = resolveFields(fPort);
        }
        Map<String, Object> result = decodeInto(selected, ctx);

        Map<String, Object> meta = new LinkedHashMap<>();
        if (metaName != null) meta.put("schema", metaName);
        if (declaresVersion) meta.put("version", declaredVersion);
        // PS-496, PS-495: a context present but malformed is refused, not dropped.
        if (context.devEUI() != null) {
            String eui = Meta.normaliseDevEui(context.devEUI());
            if (eui == null) {
                throw new SchemaException.DecodeException("devEUI " + Meta.pyRepr(context.devEUI())
                        + " is not 16 hexadecimal digits (PS-496)");
            }
            meta.put("device_eui", eui);
        }
        if (context.recvTime() != null) {
            Object rxTime = Meta.rxTimeSeconds(context.recvTime());
            if (rxTime == null) {
                throw new SchemaException.DecodeException("recvTime " + Meta.pyRepr(context.recvTime())
                        + " is not an ISO 8601 time or a number of seconds (PS-495)");
            }
            meta.put("rx_time", rxTime);
        }
        if (fPort != null) meta.put("fPort", fPort);       // PS-340, PS-342: as supplied

        List<Map<String, Object>> declaredList = new ArrayList<>();
        for (Field f : selected == null ? List.<Field>of() : selected) {
            if (f.getRaw() != null) declaredList.add(f.getRaw());
        }
        Map<String, Map<?, ?>> declared = Meta.declarations(declaredList);
        Map<String, Map<?, ?>> siblings = Wave6a.elementRepeats(declaredList);     // PS-546
        Map<String, Object> entries = new LinkedHashMap<>();
        for (String key : result.keySet()) {
            if (key.startsWith("_")) continue;              // `_quality`, `_warnings`
            Map<?, ?> source = ctx.getProducers().get(key);
            if (source == null) source = declared.get(key);
            entries.put(key, source != null ? Meta.fieldMeta(source, null, siblings) : new LinkedHashMap<>());
        }
        meta.put("fields", entries);
        result.put("_meta", meta);
        if (ctx.getRecord() != null) result.put("_decode", ctx.getRecord().asMap());   // PS-572
        return result;
    }

    /** Decodes a selected field list into a result map, with leftover bytes and warnings. */
    private Map<String, Object> decodeInto(List<Field> fieldList, DecodeContext ctx) {
        Map<String, Object> result = new LinkedHashMap<>(decodeFields(fieldList, ctx));
        ctx.reportLeftover();       // PS-472
        reportWarnings(result, ctx);
        return result;
    }

    // Decode methods
    public Map<String, Object> decode(byte[] data) {
        // PS-459, PS-460: a ports schema decoded with no FPort selects nothing. This
        // decoded the empty top-level field list and returned {} with success.
        if (ports != null && !ports.isEmpty()) {
            throw new SchemaException.DecodeException("no FPort was supplied, and schema '"
                    + name + "' selects its fields by port (PS-459)");
        }
        DecodeContext ctx = new DecodeContext(data, endian);
        ctx.setRepeatOnlyNames(repeatOnlyNames);
        Map<String, Object> result = new LinkedHashMap<>();

        // Decode main fields
        Map<String, Object> fieldsResult = decodeFields(fields, ctx);
        result.putAll(fieldsResult);
        ctx.reportLeftover();       // PS-472
        reportWarnings(result, ctx);

        return result;
    }

    public Map<String, Object> decodeWithPort(byte[] data, int fPort) {
        return decodeWithPort(data, fPort, null);
    }

    /**
     * {@link #decodeWithPort(byte[], int)} with the direction of the message supplied.
     *
     * <p>A message travelling the way the selected entry says it does not is not decoded
     * at all (PS-021): uplink bytes read through downlink field definitions produce
     * numbers with no relationship to what the device measured, and nothing in the output
     * would mark them as such, so nothing is returned (PS-288). Pass {@code null} for
     * direction to skip the check.
     */
    public Map<String, Object> decodeWithPort(byte[] data, int fPort, String direction) {
        checkDirection(fPort, direction);

        List<Field> resolvedFields = resolveFields(fPort);
        
        DecodeContext ctx = new DecodeContext(data, endian);
        ctx.setRepeatOnlyNames(repeatOnlyNames);
        Map<String, Object> result = new LinkedHashMap<>();

        // Decode resolved fields
        Map<String, Object> fieldsResult = decodeFields(resolvedFields, ctx);
        result.putAll(fieldsResult);
        ctx.reportLeftover();       // PS-472
        reportWarnings(result, ctx);

        return result;
    }

    /**
     * Copies anything the decode wanted to say into the result: per-field quality under
     * {@code _quality} and warnings under {@code _warnings}.
     *
     * <p>Each is absent unless something was collected, so a clean decode carries no extra key.
     */
    private static void reportWarnings(Map<String, Object> result, DecodeContext ctx) {
        // `_quality` first, as Go and C# order the two (PS-131, PS-427, PS-428).
        Map<String, String> quality = ctx.settledQuality();
        if (!quality.isEmpty()) {
            result.put("_quality", new LinkedHashMap<>(quality));
        }
        if (!ctx.getWarnings().isEmpty()) {
            result.put("_warnings", new ArrayList<>(ctx.getWarnings()));
        }
    }

    // Encode methods

    /**
     * Encode a data map back to payload bytes - the inverse of {@link #decode}.
     *
     * <p>Unlike the decoders, this reports per-field failures through the result rather
     * than throwing: encoding has inherently lossy cases, and a caller needs to know which
     * fields they were. See {@link Encoder} for what round-tripping can and cannot recover.
     */
    public EncodeResult encode(Map<String, Object> data) {
        return encodeWithPort(data, 0, null, null);
    }

    /**
     * {@link #encode(Map)} following a decode record (PS-573): {@code decodeRecord} is the
     * {@code _decode} that {@link #interpret(byte[], InputContext, boolean)} produced. It
     * may instead arrive in {@code data} as {@code _decode}; this argument wins.
     */
    public EncodeResult encode(Map<String, Object> data, Map<String, Object> decodeRecord) {
        return encodeWithPort(data, 0, null, decodeRecord);
    }

    /** {@link #encodeWithPort(Map, int, String)} following a decode record (PS-573). */
    public EncodeResult encodeWithPort(Map<String, Object> data, int fPort, String direction,
                                       Map<String, Object> decodeRecord) {
        // PS-581: which port entry this encode uses, and the FPort it reports. A
        // rejection is reported through the result, as every encode failure is.
        int used;
        try {
            used = chooseEncodePort(fPort, direction);
        } catch (SchemaException.DecodeException e) {
            return new EncodeResult(new byte[0], null, new ArrayList<>(List.of(e.getMessage())));
        }
        checkDirection(used, direction);
        EncodeResult result = new Encoder(this, resolveFields(used)).run(data, decodeRecord);
        result.setFPort(used);
        return result;
    }

    /**
     * The FPort whose entry an encode uses, by PS-581 (CR-2026-169). A caller's FPort
     * (non-zero; a port is 1 to 255) chooses its entry, or the {@code default} entry where
     * none lists it, and an entry declared {@code uplink} is refused only where the
     * direction is {@code downlink}. Without one: the single entry declaring
     * {@code downlink}, else the single downlink-capable entry ({@code downlink},
     * {@code both} or no declaration, PS-287), else the input is rejected naming the
     * candidates. {@code default} is never chosen without an FPort. A schema without
     * {@code ports} gives the caller's FPort, else the document's, else 0.
     */
    int chooseEncodePort(int fPort, String direction) {
        if (ports == null || ports.isEmpty()) {
            return fPort != 0 ? fPort : docFPort;
        }
        if (fPort != 0) {
            PortDef entry = ports.containsKey(String.valueOf(fPort))
                    ? ports.get(String.valueOf(fPort)) : ports.get("default");
            if (entry == null) {
                throw new SchemaException.DecodeException(String.format(
                        "FPort %d matches no port entry and schema '%s' declares no default; it defines %s (PS-581)",
                        fPort, name, joinPorts(numberedPorts(e -> true))));
            }
            if (DIRECTION_UPLINK.equals(entry.getDirection()) && DIRECTION_DOWNLINK.equals(direction)) {
                throw new SchemaException.DecodeException(String.format(
                        "FPort %d is declared direction:uplink and takes no downlink to encode (PS-581)", fPort));
            }
            return fPort;
        }
        String[] tiers = {"declared downlink", "downlink-capable"};
        for (int t = 0; t < 2; t++) {
            final boolean declaredOnly = t == 0;
            List<Integer> candidates = numberedPorts(e -> {
                String d = e.getDirection();
                return declaredOnly ? DIRECTION_DOWNLINK.equals(d)
                        : d == null || DIRECTION_DOWNLINK.equals(d) || DIRECTION_BOTH.equals(d);
            });
            if (candidates.size() == 1) return candidates.get(0);
            if (candidates.size() > 1) {
                throw new SchemaException.DecodeException(String.format(
                        "no FPort was given, and schema '%s' has %d %s port entries (%s); give the FPort (PS-581)",
                        name, candidates.size(), tiers[t], joinPorts(candidates)));
            }
        }
        throw new SchemaException.DecodeException(String.format(
                "no FPort was given, and schema '%s' has no port entry that takes a downlink (PS-581)", name));
    }

    private List<Integer> numberedPorts(java.util.function.Predicate<PortDef> ok) {
        List<Integer> out = new ArrayList<>();
        for (Map.Entry<String, PortDef> e : ports.entrySet()) {
            if (e.getValue() == null || !ok.test(e.getValue())) continue;
            try { out.add(Integer.parseInt(e.getKey())); } catch (NumberFormatException ignored) { }
        }
        Collections.sort(out);
        return out;
    }

    private static String joinPorts(List<Integer> ports) {
        if (ports.isEmpty()) return "nothing";
        StringBuilder sb = new StringBuilder();
        for (int p : ports) { if (sb.length() > 0) sb.append(", "); sb.append(p); }
        return sb.toString();
    }

    /** PS-572: the JSON Pointer of each node of the expanded document, by identity. */
    Map<Object, String> pointers() { return pointers; }

    /** {@link #encode} with port-based schema selection. */
    public EncodeResult encodeWithPort(Map<String, Object> data, int fPort) {
        return encodeWithPort(data, fPort, null);
    }

    /**
     * {@link #encodeWithPort(Map, int)} with the direction the message will travel
     * supplied.
     *
     * <p>The mirror of the decode check (PS-292): encoding for an entry that disclaims
     * this direction produces bytes the far end reads against different field
     * definitions, so nothing is encoded.
     */
    public EncodeResult encodeWithPort(Map<String, Object> data, int fPort, String direction) {
        return encodeWithPort(data, fPort, direction, null);
    }

    /** Directions a message can be travelling, as passed to the three-argument
     * decode and encode methods (PS-290). */
    public static final String DIRECTION_UPLINK = "uplink";
    public static final String DIRECTION_DOWNLINK = "downlink";
    public static final String DIRECTION_BOTH = "both";

    /**
     * Values {@code direction} may take on a schema or a port entry (PS-287).
     * {@code bidirectional} appeared in a clause 5 example and is not one of them;
     * CR-2026-010 withdrew that spelling so a schema carrying it surfaces rather than
     * being read as {@code both}.
     */
    private static final Set<String> DECLARED_DIRECTIONS =
            Set.of(DIRECTION_UPLINK, DIRECTION_DOWNLINK, DIRECTION_BOTH);

    /**
     * Throws where handling a message of this direction contradicts the entry the decode
     * or encode would use (PS-021), and returns quietly where no check applies: the
     * caller stated no direction (PS-290), or the entry declares {@code both}, or it
     * declares nothing, which PS-287 reads as {@code both}.
     *
     * <p>Before this, {@code PortDef.getDirection()} had no call site. An uplink on a
     * port declared {@code direction: downlink} decoded against that port's fields and
     * came back as command=0, reporting_interval=60225 - three bytes that were a
     * temperature and a humidity, reported as a configuration value with no error.
     */
    private void checkDirection(int fPort, String direction) {
        if (direction == null || direction.isEmpty()) {
            return;
        }
        if (!DIRECTION_UPLINK.equals(direction) && !DIRECTION_DOWNLINK.equals(direction)) {
            throw new IllegalArgumentException(String.format(
                    "unknown message direction \"%s\"; expected one of downlink, uplink", direction));
        }

        // Mirrors resolveFields' selection order, so the direction checked and the fields
        // used come from the same entry (PS-289). The label distinguishes a matched port
        // from the default entry standing in for one: naming "fPort 42" of a payload the
        // default entry accepted describes the wrong thing.
        String declared;
        String label;
        if (ports == null) {
            declared = this.direction;
            label = String.format("schema '%s'", name);
        } else if (ports.containsKey(String.valueOf(fPort))) {
            declared = ports.get(String.valueOf(fPort)).getDirection();
            label = "fPort " + fPort;
        } else if (ports.containsKey("default")) {
            declared = ports.get("default").getDirection();
            label = "the default port entry";
        } else {
            // No entry to check. resolveFields reports this.
            return;
        }

        if (declared == null || DIRECTION_BOTH.equals(declared) || declared.equals(direction)) {
            return;
        }
        if (!DECLARED_DIRECTIONS.contains(declared)) {
            throw new SchemaException.DecodeException(String.format(
                    "%s declares unknown direction \"%s\"; expected both, downlink, uplink",
                    label, declared));
        }
        throw new SchemaException.DecodeException(String.format(
                "%s is declared direction:%s; message direction is %s", label, declared, direction));
    }

    private List<Field> resolveFields(int fPort) {
        if (ports == null) {
            return fields;
        }
        
        String portKey = String.valueOf(fPort);
        if (ports.containsKey(portKey)) {
            return ports.get(portKey).getFields();
        }
        if (ports.containsKey("default")) {
            return ports.get("default").getFields();
        }
        
        throw new SchemaException.DecodeException(
            String.format("No port definition for fPort %d and no default in schema '%s'", fPort, name));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> decodeFields(List<Field> fieldList, DecodeContext ctx) {
        Map<String, Object> result = new LinkedHashMap<>();
        
        for (Field field : fieldList) {
            // PS-402, PS-403: an optional field is decoded where its bytes remain and is
            // absent where none do. Every later field is optional too (PS-404), so once one
            // is absent the list ends. Some bytes but too few is an error: an optional
            // field is never partly read.
            if (field.isOptional()) {
                int remaining = ctx.remaining();
                if (remaining <= 0) break;
                int size = Wave6b.optionalFieldSize(field, Schema::fixedElementSize);
                if (size > 0 && remaining < size) {
                    throw new SchemaException.DecodeException(String.format(
                            "Error decoding %s: optional field takes %d byte(s) but %d remain at offset %d (PS-403)",
                            field.getName() == null ? "?" : field.getName(), size, remaining, ctx.getOffset()));
                }
            }

            // PS-383: a repeat's trailer is decoded from the reserved bytes before its
            // first element, so its names are bound for the elements, and it is reported
            // beside the repeat.
            if (field.getType() == FieldType.REPEAT && field.getTrailer() != null
                    && !field.getTrailer().isEmpty()) {
                decodeTrailer(field, ctx, result);
            }

            // Handle TLV
            if (field.getType() == FieldType.TLV) {
                Map<String, Object> tlvResult = decodeTLV(field, ctx);
                result.putAll(tlvResult);
                continue;
            }
            
            // Handle TLV inline
            if (field.getTlvInline() != null) {
                Map<String, Object> tlvResult = decodeTLV(field.getTlvInline(), ctx);
                result.putAll(tlvResult);
                continue;
            }
            
            // Handle an inline match block. Its selected case contributes its fields
            // flat alongside the block's siblings, as the interpreter does.
            if (field.getMatchInline() != null) {
                Object matchResult = decodeMatch(field.getMatchInline(), ctx, true);
                if (matchResult instanceof Map<?, ?> matchMap) {
                    for (Map.Entry<?, ?> entry : matchMap.entrySet()) {
                        String key = String.valueOf(entry.getKey());
                        result.put(key, entry.getValue());
                        // A looked-up field in the case bound itself (PS-523); rebinding
                        // its reported label here would undo that.
                        if (!ctx.hasLabel(key)) ctx.setVariable(key, entry.getValue());
                    }
                }
                continue;
            }

            // PS-541: each field list yields one record, which the enclosing repeat
            // reports in place of the element.
            if (field.getRecords() != null) {
                if (ctx.getPendingRecords() == null) {
                    throw new SchemaException.DecodeException("`records` belongs in a repeat's element, "
                            + "or a match case within it (PS-541)");
                }
                Object bodies = field.getRaw() == null ? null : field.getRaw().get("records");
                for (int n = 0; n < field.getRecords().size(); n++) {
                    DecodeRecord rec = ctx.getRecord();
                    // PS-572: a record's location is `records/n[0]`.
                    boolean pushed = rec != null && rec.push(bodies, "records", 0, "/" + n);
                    try {
                        ctx.getPendingRecords().add(decodeFields(field.getRecords().get(n), ctx));
                    } finally {
                        if (rec != null) rec.pop(pushed);
                    }
                }
                continue;
            }

            // Handle byte_group: every member reads from the group's first byte, and
            // the cursor advances once, by the group's size, after all of them.
            if (field.getByteGroup() != null) {
                Map<String, Object> groupResult = decodeByteGroup(field, ctx);
                result.putAll(groupResult);
                continue;
            }

            // Handle flagged construct
            if (field.getFlagged() != null) {
                Map<String, Object> flaggedResult = decodeFlagged(field.getFlagged(), ctx);
                result.putAll(flaggedResult);
                for (Map.Entry<String, Object> entry : flaggedResult.entrySet()) {
                    if (!ctx.hasLabel(entry.getKey())) {                // PS-523
                        ctx.setVariable(entry.getKey(), entry.getValue());
                    }
                }
                continue;
            }
            
            // PS-490: the declaration that writes this key, recorded before it is decoded
            // as the reference does. An object's members and a repeat's elements are
            // another level of output, described by the declaration (PS-371, PS-481).
            ctx.produced(field, null);
            boolean nestedLevel = field.getType() == FieldType.OBJECT
                    || field.getType() == FieldType.REPEAT;
            Object value;
            if (nestedLevel) ctx.enterMetaLevel();
            try {
                value = decodeField(field, ctx);
            } finally {
                if (nestedLevel) ctx.exitMetaLevel();
            }

            // PS-427, PS-428: no reading, recorded in `_quality` where it is produced. An
            // internal field is only left unbound, except inside a flagged group, where
            // the reference marks it as well (as Go's decodeFields does).
            if (value == SENTINEL_ABSENT || value == RANGE_ABSENT) {
                boolean internal = field.getName() != null && field.getName().startsWith("_");
                if (field.getName() != null && (!internal || ctx.inFlagged())) {
                    ctx.markAbsent(field, value == SENTINEL_ABSENT ? "absent" : "out_of_range");
                }
                continue;
            }

            if (value == OMITTED) {
                // A mapping lookup with no entry and no default (PS-269): not reported,
                // and a reference to it still binds the number (PS-523).
                if (field.getName() != null && !field.getName().isEmpty()) {
                    ctx.bindMissed(field.getName(), field);
                }
                continue;
            }

            if (value != null && field.getName() != null && !field.getName().isEmpty()) {
                // A leading underscore marks an internal field: it becomes a variable
                // later fields can reference, but is not reported. Without this an
                // intermediate used to combine two words appeared in the output.
                if (!field.getName().startsWith("_")) {
                    Object reported = normalizeOutput(value);
                    if (reported != null) {
                        String outputName = resolveFieldName(field, ctx);
                        if (!outputName.equals(field.getName())) {
                            ctx.produced(field, outputName);        // PS-492
                        }
                        result.put(outputName, reported);
                    }
                }
                else recordInternal(ctx, field.getRaw(), value);        // PS-572
                // Variables are keyed by the schema-level name so $references keep
                // working when name_from is in play (PS-267).
                ctx.bind(field.getName(), field, value);       // PS-523
                ctx.checkValidRange(value, field);     // PS-131
            }
        }
        
        return result;
    }

    /**
     * Decode a repeat's trailer from the last {@code reserve} bytes of the region into the
     * enclosing scope (PS-383): bound for the elements and every later field, and reported
     * as siblings of the repeat. The read position is left where it was, for the elements.
     */
    private void decodeTrailer(Field repeat, DecodeContext ctx, Map<String, Object> result) {
        int reserve = repeat.getReserve();
        int start = ctx.getLimit() - reserve;
        if (start < ctx.getOffset()) {
            throw new SchemaException.DecodeException(String.format(
                    "repeat '%s' reserves %d byte(s) but %d remain at offset %d (PS-351)",
                    repeat.getName(), reserve, ctx.remaining(), ctx.getOffset()));
        }
        int saved = ctx.getOffset();
        ctx.setOffset(start);
        try {
            result.putAll(decodeFields(repeat.getTrailer(), ctx));
        } finally {
            ctx.setOffset(saved);
        }
    }

    private Map<String, Object> decodeFlagged(Field.FlaggedDef fd, DecodeContext ctx) {
        // The mask is a reference like any other, so it reads the PS-523 binding that
        // DecodeContext.bind made: the number under a text or boolean label, a numeric
        // label itself, an enum's code. Written `$mask` it found nothing.
        String maskName = fd.getField() == null ? "" : fd.getField();
        if (maskName.startsWith("$")) maskName = maskName.substring(1);
        Object flagsVal = ctx.getVariable(maskName);
        if (flagsVal == null) {
            throw new SchemaException.DecodeException("Flagged field reference not found: " + fd.getField());
        }
        int flags = toInt(flagsVal, 0);
        if (ctx.getRecord() != null) {                                      // PS-572
            String where = ctx.getRecord().loc(fd.getRaw());
            if (where != null) ctx.getRecord().flagged.put(where, (long) flags);
        }
        
        Map<String, Object> result = new LinkedHashMap<>();
        
        ctx.enterFlagged();
        try {
            for (Field.FlaggedGroup group : fd.getGroups()) {
                int isPresent = (flags >> group.getBit()) & 1;
                if (isPresent != 0) {
                    Map<String, Object> groupResult = decodeFields(group.getFields(), ctx);
                    result.putAll(groupResult);
                }
            }
        } finally {
            ctx.exitFlagged();
        }
        
        return result;
    }

    private Object decodeField(Field field, DecodeContext ctx) {
        return decodeField(field, ctx, true);
    }

    /**
     * @param fieldRules whether the per-field rules of wave 6b apply - {@code sentinel}
     *                   (PS-427) and {@code out_of_range: omit} (PS-428). They do in a field
     *                   list and not to a byte_group member, as in the reference.
     */
    private Object decodeField(Field field, DecodeContext ctx, boolean fieldRules) {
        try {
            return decodeFieldBody(field, ctx, fieldRules);
        } catch (SchemaException.BufferUnderflow e) {
            // PS-535: the innermost field that read past the end is named; an enclosing
            // object, repeat or construct passes its member's error through unchanged.
            throw e.named(field.getName(), kindOf(field));
        }
    }

    /** What a field is, as its schema wrote it, for a PS-535 error with no name to give. */
    private static String kindOf(Field field) {
        Object raw = field.getRaw() != null ? field.getRaw().get("type") : null;
        if (raw != null) return String.valueOf(raw);
        return field.getType() != null ? field.getType().name().toLowerCase() : "field";
    }

    private Object decodeFieldBody(Field field, DecodeContext ctx, boolean fieldRules) {
        int length = field.getEffectiveLength();
        if (field.getLengthRef() != null) {
            switch (field.getType()) {
                // PS-464, PS-465: a length naming a preceding field, resolved from the
                // variables as a repeat's byte_length is.
                case BYTES, ASCII, HEX, BASE64, SKIP -> length = Wave6b.resolveLength(field, ctx);
                default -> { }
            }
        }
        String fieldEndian = field.getEffectiveEndian(ctx.getEndian());
        
        Object value = null;
        // The integer read before an `encoding` decoded it, for the sentinel (PS-427).
        Object preEncoding = null;
        // A failed guard's `else`, which no lookup reaches (PS-444).
        boolean asDeclared = false;
        
        switch (field.getType()) {
            // The type fixes both orders, so fieldEndian is deliberately not consulted
            // (PS-272): honouring it would make u32le16 with endian little a second
            // spelling of little-endian u32.
            case U32LE16, S32LE16, F32LE16, U32BE16LE, S32BE16LE, F32BE16LE -> {
                value = Wave4.readWordOrdered(field.getType(), ctx.read(4));
            }

            case UFLT16, SFLT16, SFLT24 -> {
                // The MCCI minifloats (CR-2026-063): a word in the field's byte order.
                byte[] data = ctx.read(Wave4.minifloatSize(field.getType()));
                Double decoded = Wave4.decodeMinifloat(field.getType(), ctx.decodeUnsigned(data, fieldEndian));
                if (decoded == null) return OMITTED;     // no value JSON can carry (PS-419)
                value = decoded;
            }

            case U8, U16, U24, U32, U64, BYTE, UINT -> {
                int start = ctx.getOffset();
                byte[] data = ctx.read(length);
                long raw = ctx.decodeUnsigned(data, fieldEndian);
                if (field.getChecksum() != null) {
                    // PS-586 (CR-2026-174): the check value against the bytes it covers,
                    // from `from` to the byte before the field, read before any modifier.
                    String mismatch = Checksum.mismatch(field.getName(), field.getChecksum(),
                            ctx.getData(), start, raw);
                    if (mismatch != null) {
                        if (Checksum.warns(field.getChecksum())) ctx.addWarning(mismatch);
                        else throw new SchemaException.DecodeException(mismatch);
                    }
                }
                if (field.getEncoding() != null) {
                    preEncoding = raw;
                    // A sentinel is a bit pattern, which need not be a valid code: 0xFF is
                    // no BCD number, and must be absent rather than an error (PS-427).
                    if (fieldRules && Wave6b.sentinelHit(field, raw)) return SENTINEL_ABSENT;
                    // Applied to the integer read, before the modifiers (PS-422).
                    value = Wave4.decodeEncoding(raw, field.getEncoding(), length, field.getName());
                } else if (length >= 8 && raw < 0) {
                    // A u64 at or above 2^63 does not fit a Java long: the bit pattern
                    // reads as a negative number, and this decoder reported -1 for
                    // 18446744073709551615. PS-295 forbids a sign-changed value and
                    // permits the exact value as a decimal string, which is what an
                    // unsigned reading of the same bits is.
                    value = Long.toUnsignedString(raw);
                } else {
                    value = raw;
                }
            }

            case I8, I16, I24, I32, I64, S8, S16, S24, S32, S64, SINT -> {
                byte[] data = ctx.read(length);
                value = ctx.decodeSigned(data, fieldEndian);
            }
            
            case BINT -> {
                byte[] data = ctx.read(length);
                value = ctx.decodeUnsigned(data, "big");
            }
            
            case BF16 -> {
                // CR-2026-149 (PS-558): the upper half of a binary32, a word in the field's
                // effective byte order.
                byte[] data = ctx.read(2);
                value = Bf16.decode(ctx.decodeUnsigned(data, fieldEndian));
            }

            case F16, F32, F64, FLOAT16, FLOAT32, FLOAT64 -> {
                int size = switch (field.getType()) {
                    case F16, FLOAT16 -> 2;
                    case F32, FLOAT32 -> 4;
                    case F64, FLOAT64 -> 8;
                    default -> 4;
                };
                byte[] data = ctx.read(size);
                value = ctx.decodeFloat(data, size, fieldEndian);
            }
            
            case UDEC, SDEC -> {
                // Nibble-decimal (PS-330): upper nibble the whole part, a 4-bit
                // two's-complement value for sdec; lower nibble the tenths.
                int b = ctx.read(1)[0] & 0xFF;
                int whole = b >> 4;
                if (field.getType() == FieldType.SDEC && whole >= 8) whole -= 16;
                value = whole + (b & 0x0F) * 0.1;
            }

            case BOOL -> {
                // PS-065/066: one bit of the current byte, the spec's `bit:` key naming
                // it, and no advance unless `consume` says so. `bit:` was never read,
                // so every bool decoded bit 0; and `consume` was ignored, so a group of
                // flags ending in `consume: 1` left the cursor on the flag byte and the
                // next field read it again (dnt/dnt-lw-wsci: 26 vectors).
                byte[] data = ctx.peek(1, field.getByteOffset());
                int bit = field.getBoolBit() >= 0 ? field.getBoolBit() : field.getBitOffset();
                value = ctx.decodeBits(data[0] & 0xFF, bit, 1) != 0;
                if (field.getConsume() > 0) {
                    ctx.read(field.getConsume());
                }
            }
            
            case BITS -> {
                // Read the whole base width, not just the first byte: a range wider
                // than a byte (u24[0:11] for a packed 12-bit humidity) decoded from
                // byte zero alone and reported a value with no error.
                int baseBytes = Math.max(1, field.getBitBaseBytes());
                byte[] data = ctx.peek(baseBytes, field.getByteOffset());
                // PS-059 (CR-2026-052): assembled in the field's effective byte order,
                // which was big-endian whatever the schema said.
                long base = ctx.decodeUnsigned(data, fieldEndian);
                int numBits = field.getBits() > 0 ? field.getBits() : 1;
                value = extractRange(base, field.isSignedBits(), field.getBitOffset(), numBits);
                // An explicit range does not advance the cursor by itself: several
                // fields share one byte and the last of them declares `consume`.
                if (field.getConsume() > 0) {
                    ctx.read(field.getConsume());
                }
            }
            
            case ASCII -> {
                // PS-588 (CR-2026-175): `consume: 0` re-reads the span; the position stays.
                int echoStart = ctx.getOffset();
                byte[] data = ctx.read(length);
                if (field.isEcho()) ctx.setOffset(echoStart);
                // PS-538: each byte is the ISO 8859-1 character of the same number, so the
                // value is lossless and re-encodes to the same bytes (PS-537). US_ASCII
                // decoded a byte above 0x7F as U+FFFD. PS-073's warning reports it.
                String str = new String(data, StandardCharsets.ISO_8859_1);
                for (byte b : data) {
                    if ((b & 0xFF) > 0x7F) {
                        ctx.addWarning("Field '" + field.getName()
                                + "': a byte above 0x7F in an ascii value (PS-073, PS-538)");
                        break;
                    }
                }
                // Trailing NUL padding only, as the reference strips it: this removed
                // every NUL and then trimmed spaces, so "HC " read as "HC" and "A\0B"
                // as "AB" where Python, Go and C# keep both.
                int end = str.length();
                while (end > 0 && str.charAt(end - 1) == '\0') end--;
                value = str.substring(0, end);
                if (field.getNumeral() != null) {
                    value = Numeral.decode(field.getName(), (String) value, field.getNumeral());   // PS-560
                }
            }
            
            case HEX -> {
                // PS-588 (CR-2026-175): `consume: 0` re-reads the span; the position stays.
                int echoStart = ctx.getOffset();
                byte[] data = ctx.read(length);
                if (field.isEcho()) ctx.setOffset(echoStart);
                value = bytesToHex(data);
            }

            case BASE64 -> {
                // RFC 4648 base64 of the bytes read. The type parsed and had no decode
                // case, so every `type: base64` field failed as an unknown type.
                value = Base64.getEncoder().encodeToString(ctx.read(length));
            }
            
            case SKIP -> {
                ctx.read(length);
                return null;
            }
            
            case BYTES -> {
                // PS-588 (CR-2026-175): `consume: 0` re-reads the span; the position stays.
                int echoStart = ctx.getOffset();
                byte[] data = ctx.read(length);
                if (field.isEcho()) ctx.setOffset(echoStart);
                value = formatBytes(data, field.getFormat(), field.getSeparator());
            }
            
            case REPEAT -> {
                value = decodeRepeat(field, ctx);
            }
            
            case BITFIELD_STRING -> {
                byte[] data = ctx.read(length);
                long intVal = ctx.decodeUnsigned(data, fieldEndian);
                value = decodeBitfieldString(intVal, field);
            }
            
            case STRING -> {
                // A literal reads no bytes (PS-357), and `string` is only ever a literal: a
                // string read from the payload is `ascii` (PS-361). A string with no value
                // reported null.
                if (field.getValue() == null) {
                    throw new SchemaException.DecodeException("field '" + field.getName()
                            + "': type string declares no value; a string read from the payload "
                            + "is type ascii (PS-361)");
                }
                value = field.getValue();
            }

            case BOOLEAN -> {
                // A boolean literal reads no bytes (PS-357, PS-519); checked at load (PS-520).
                if (field.getValue() == null) {
                    throw new SchemaException.DecodeException("field '" + field.getName()
                            + "': type boolean is a literal and declares no value (PS-519)");
                }
                value = field.getValue();
            }

            case NUMBER -> {
                value = decodeComputed(field, ctx);
                if (value instanceof GuardElse fallback) {
                    value = fallback.value();
                    asDeclared = true;
                }
                if (field.isIntegerResult() && value instanceof Number n
                        && !Double.isNaN(n.doubleValue()) && !Double.isInfinite(n.doubleValue())) {
                    double d = n.doubleValue();
                    // PS-388: a fractional part is an error, never truncated or rounded.
                    if (d != Math.rint(d) || Double.isInfinite(d)) {
                        throw new SchemaException.DecodeException(field.getName()
                                + ": type integer but the computed value is " + d
                                + "; add `idiv` to truncate or a {op: round} transform stage");
                    }
                    value = (long) d;
                }
            }

            case ENUM -> {
                int baseLength = switch (field.getBase() == null ? "u8" : field.getBase()) {
                    case "u16", "s16" -> 2;
                    case "u24", "s24" -> 3;
                    case "u32", "s32" -> 4;
                    default -> 1;
                };
                byte[] data = ctx.read(baseLength);
                int raw = (int) ctx.decodeUnsigned(data, fieldEndian);
                // PS-523 (CR-2026-132): a reference to an enum binds its code.
                ctx.recordEnumCode(field, (long) raw);
                Map<Integer, String> values = field.getValues();
                if (values != null && values.containsKey(raw)) {
                    value = values.get(raw);
                } else if (field.getEnumDefault() != null) {
                    // An unmapped value reports the declared default (PS-068).
                    value = field.getEnumDefault();
                } else {
                    // No name and no default: omitted in applyGroupMember (PS-068, as
                    // amended by CR-2026-132), still bound to the code.
                    value = (long) raw;
                }
            }
            
            case OBJECT -> {
                value = decodeFields(field.getFields(), ctx);
            }
            
            case MATCH, SWITCH -> {
                value = decodeMatch(field, ctx, false);
            }
            
            case TLV -> {
                return decodeTLV(field, ctx);
            }
            
            default -> throw new SchemaException.DecodeException("Unknown field type: " + field.getType());
        }

        // PS-427: a reserved raw value means no reading, so the field is absent - neither
        // reported nor bound. Compared with the integer read, before any modifier and
        // before an encoding is decoded: it is a bit pattern, not a quantity.
        if (fieldRules && field.getType() != FieldType.NUMBER
                && Wave6b.sentinelHit(field, preEncoding != null ? preEncoding : value)) {
            return SENTINEL_ABSENT;
        }
        
        return applyGroupMember(value, field, ctx, fieldRules, asDeclared);
    }

    /**
     * What happens to a value once it is read: modifiers, lookup, and the
     * field's variable. Split out so a byte_group member, whose value comes from the
     * group's assembled bytes (PS-364), runs the same pipeline.
     */
    private Object applyGroupMember(Object value, Field field, DecodeContext ctx) {
        return applyGroupMember(value, field, ctx, false, false);
    }

    /**
     * @param asDeclared the value is a failed guard's {@code else}, which the lookup does
     *                   not reach (PS-444): {@code else: 7} beside a lookup reported the
     *                   label at index 7.
     */
    private Object applyGroupMember(Object value, Field field, DecodeContext ctx, boolean fieldRules,
            boolean asDeclared) {
        ctx.clearLookup(field);
        // A computed field has already
        // had its own arithmetic applied by decodeComputed, in the order the
        // interpreter uses: polynomial, then modifiers, then transform. Running the
        // block below over it again would apply the modifiers twice, and would apply
        // them to a guard's fallback, which must be reported as declared.
        if (reportsAsInteger(field)) {
            // PS-293, PS-294: an integer-typed field carrying no modifier keeps the exact
            // long it was read as. The doubleValue() below is what used to lose it - a u64
            // of 2^53+1 came back as 9007199254740992.
        } else if (value instanceof Number && field.getType() != FieldType.NUMBER) {
            value = applyArithmetic(((Number) value).doubleValue(), field);
        }
        
        // A value the arithmetic could not produce - a zero divisor (PS-100), the log of
        // a non-positive number (PS-117) - is absent, and NaN is never reported (PS-282).
        // Checked before the lookup, which would read NaN as index 0.
        if (value instanceof Double d && (d.isNaN() || d.isInfinite())) {
            return OMITTED;
        }

        // PS-475: valid_range compares the value after the arithmetic and before the
        // lookup, so a looked-up field is compared on its number, not its label. A value
        // omitted by `out_of_range: omit` takes no further step: it is not looked up, so
        // an index the sequence lacks is no error when the field is dropped anyway. A
        // failed guard's `else` ends the sequence and is compared with nothing (PS-443).
        boolean lookedUp = field.getLookup() != null && value instanceof Number && !asDeclared;
        ctx.recordRangeInputs(field, asDeclared, lookedUp, value);
        if (lookedUp && fieldRules && Wave6b.rangeOmits(field, value)) {
            return RANGE_ABSENT;
        }

        // Apply lookup. A mapping's keys need not start at zero or be contiguous
        // (PS-268); an unmatched value omits the field rather than reporting the raw
        // integer under a name that promises a label, unless a default is declared
        // (PS-269). A sequence is indexed from zero (PS-104) and an out-of-bounds
        // index is an error (PS-105), not the raw value: the payload does not match
        // the schema's shape at all.
        if (field.getLookup() != null && value instanceof Number && !asDeclared) {
            // A value with a fraction matches no key: intValue() would truncate 2.5 to
            // the key 2. An integral double such as 7.0 is the key 7. A computed value
            // reaches the lookup after its arithmetic (PS-443), so it is often a double.
            double numVal = ((Number) value).doubleValue();
            // PS-523: the number a reference binds where the label is not a number.
            Object number = value;
            int intVal = (int) numVal;
            boolean integral = numVal == Math.rint(numVal) && !Double.isInfinite(numVal);
            String template = Wave5.template(field);
            if (integral && field.getLookup().containsKey(intVal)) {
                value = field.getLookup().get(intVal);
            } else if (template != null) {
                // PS-406: the default names the value it could not map.
                value = template.replace(Wave5.VALUE_TOKEN, Wave5.formatLookupValue(numVal));
            } else if (field.getLookupDefault() != null) {
                value = field.getLookupDefault();
            } else if (field.isLookupSequence() && !integral) {
                // PS-105: a value with a fraction is no index of the sequence. Reported
                // as the index it truncated to, which read as an out-of-bounds integer.
                throw new SchemaException.DecodeException(String.format(
                    "lookup index %s is not an index of a %d-entry sequence (PS-105)",
                    Wave5.formatLookupValue(numVal), field.getLookup().size()));
            } else if (field.isLookupSequence()) {
                throw new SchemaException.DecodeException(String.format(
                    "lookup index %d out of bounds for %d entries",
                    intVal, field.getLookup().size()));
            } else {
                // PS-269 omits the field; PS-523 leaves a reference bound to the number.
                ctx.recordLookup(field, number, DecodeContext.NO_LABEL);
                if (field.getVar() != null && !field.getVar().isEmpty()) {
                    ctx.bind(field.getVar(), field, number);
                }
                return OMITTED;
            }
            ctx.recordLookup(field, number, value);
        }

        // PS-523 (CR-2026-132): an enum is a lookup by another spelling - its code is the
        // number a reference binds, its name the label a template writes.
        if (field.getType() == FieldType.ENUM) {
            Object code = ctx.takeEnumCode(field);
            if (code != null) {
                Map<Integer, String> names = field.getValues();
                boolean named = names != null && names.containsKey(((Number) code).intValue());
                if (!named && field.getEnumDefault() == null) {
                    // PS-068 (CR-2026-132): a code with no name and no default is omitted,
                    // as a lookup miss is (PS-269) - never the raw integer - and a
                    // reference still binds the code (PS-523).
                    ctx.recordLookup(field, code, DecodeContext.NO_LABEL);
                    if (field.getVar() != null && !field.getVar().isEmpty()) {
                        ctx.bind(field.getVar(), field, code);
                    }
                    return OMITTED;
                }
                ctx.recordLookup(field, code, value);
            }
        }
        
        // PS-428: outside the range is no reading, not a flagged one, so the field is
        // absent - neither reported nor bound.
        if (fieldRules && !asDeclared && !lookedUp && Wave6b.rangeOmits(field, value)) {
            return RANGE_ABSENT;
        }

        // Store variable
        if (field.getVar() != null && !field.getVar().isEmpty()) {
            ctx.bind(field.getVar(), field, value);        // PS-523
        }
        
        return value;
    }

    /**
     * Whether this field's declared type selects `integer` in the clause 1 table and
     * nothing in the field turns it into a `number` (PS-279, PS-293).
     */
    private static boolean reportsAsInteger(Field field) {
        switch (field.getType()) {
            case U8: case U16: case U24: case U32: case U64: case BYTE: case UINT:
            case I8: case I16: case I24: case I32: case I64:
            case S8: case S16: case S24: case S32: case S64: case SINT:
            case BINT: case U32LE16: case S32LE16:
                break;
            default:
                return false;
        }
        return field.getMult() == null && field.getDiv() == null && field.getAdd() == null
                && (field.getTransform() == null || field.getTransform().isEmpty());
    }

    /** PS-572: the case a match took and the discriminator it read, at its location. */
    private static void recordMatch(boolean record, Field field, DecodeContext ctx, Object key,
                                    Object discriminator) {
        if (!record || ctx.getRecord() == null) return;
        String where = ctx.getRecord().loc(field.getRaw());
        if (where == null) return;
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("case", key == null ? "default" : DecodeRecord.caseKey(key));
        entry.put("value", normalizeOutput(discriminator));
        ctx.getRecord().matches.put(where, entry);
    }

    /** PS-572: an internal field's decoded value, at its location in the decode record. */
    private static void recordInternal(DecodeContext ctx, Object raw, Object value) {
        DecodeRecord rec = ctx.getRecord();
        if (rec == null || value == null || value == OMITTED) return;
        String where = rec.loc(raw);
        if (where == null) return;
        Object reported = normalizeOutput(value);
        if (reported != null) rec.internal.put(where, reported);
    }

    private Object decodeMatch(Field field, DecodeContext ctx, boolean record) {
        // A long, not an int. A bit-range peek reports a Double, and narrowing a Double
        // above Integer.MAX_VALUE saturates to 2147483647 while narrowing the Long case
        // key 2147483648 wraps to -2147483648 - so a u32 discriminator of 0x80000000 never
        // equalled the key that spells it, and the construct took its default instead.
        // Null where the discriminator is no integer at all - a u64 above 2^63-1, say -
        // which then matches no case rather than the 0 a default conversion would give.
        Long matchValue;
        Object discriminator;
        // What this construct contributes on its own: the discriminator under `name`,
        // where it was read here and named. Null where there is nothing to report.
        Map<String, Object> inline = null;
        
        if (field.isMatchRemaining()) {
            // PS-414: the bytes from here to the end of the region, which a reserving
            // repeat or tlv has already narrowed. Nothing is read.
            matchValue = (long) ctx.remaining();
            discriminator = matchValue;
        } else if (field.getOn() != null && !field.getOn().isEmpty()) {
            String varName = field.getOn().startsWith("$") ? field.getOn().substring(1) : field.getOn();
            Object val = ctx.getVariable(varName);
            if (val == null) {
                throw new SchemaException.DecodeException("Variable not found: $" + varName);
            }
            matchValue = asLong(val);
            discriminator = val;
            if (val instanceof String text) {
                // A text discriminator (an `ascii` field) matches a key of the same text,
                // as the reference compares it (_match_case_pattern).
                for (Field.Case c : field.getCases()) {
                    if (!c.isDefault() && text.equals(String.valueOf(c.getCaseValue()))) {
                        recordMatch(record, field, ctx, c.getCaseValue(), discriminator);
                        return mergeMatch(inline, decodeFields(c.getFields(), ctx));
                    }
                }
            }
            // A computed discriminator may carry a fraction, which equals no integer key:
            // asLong truncated it, so 2.5 selected case 2 (PS-484's reading, applied to
            // case keys). Only an inclusive range can hold it (PS-398).
            if (val instanceof Number number && !(val instanceof Boolean)
                    && number.doubleValue() != Math.rint(number.doubleValue())
                    && !Double.isInfinite(number.doubleValue())) {
                for (Field.Case c : field.getCases()) {
                    if (!c.isDefault() && c.getCaseValue() != null
                            && fractionInRange(number.doubleValue(), c.getCaseValue())) {
                        recordMatch(record, field, ctx, c.getCaseValue(), discriminator);
                        return mergeMatch(inline, decodeFields(c.getFields(), ctx));
                    }
                }
                matchValue = null;
            }
        } else {
            int length = field.getLength() > 0 ? field.getLength() : 1;
            byte[] data;
            try {
                data = ctx.read(length);
            } catch (SchemaException.BufferUnderflow e) {
                // PS-535: the discriminator is the field that read; it is named by the
                // match's `name`, where it declares one.
                throw e.named(field.getName(), "match");
            }
            matchValue = ctx.decodeUnsigned(data, ctx.getEndian());
            discriminator = matchValue;

            // A discriminator read from the payload is reported where `name` asks for it
            // and stored where `var` does (CR-2026-020). Both were parsed and then
            // discarded, so a schema naming its discriminator decoded one field fewer
            // and a later `$ref` to the variable resolved to nothing. One taken from
            // `field` needs neither: it is already in the output under its own name.
            if (field.getVar() != null && !field.getVar().isEmpty()) {
                ctx.setVariable(field.getVar(), matchValue);
            }
            if (field.getName() != null && !field.getName().isEmpty()) {
                inline = new LinkedHashMap<>();
                inline.put(field.getName(), matchValue);
                ctx.setVariable(field.getName(), matchValue);
            }
        }

        // parseCaseMap sorts a default case last, so reaching one here means no explicit
        // case matched.
        for (Field.Case c : field.getCases()) {
            if (c.isDefault()) {
                recordMatch(record, field, ctx, null, discriminator);
                return mergeMatch(inline, decodeFields(c.getFields(), ctx));
            }

            Object caseVal = c.getCaseValue();
            if (caseVal == null) continue;

            if (matchValue != null && matchesCase(matchValue, caseVal)) {
                recordMatch(record, field, ctx, caseVal, discriminator);
                return mergeMatch(inline, decodeFields(c.getFields(), ctx));
            }
        }

        // `default` decides what an unmatched value means and defaults to "error".
        // Nothing read the key, so every value of it behaved as "skip" and a schema
        // declaring a fallback got none.
        Object fallback = field.getMatchDefault();
        if (fallback instanceof List<?> fallbackFields) {
            recordMatch(record, field, ctx, null, discriminator);
            // Already Fields: the parser converts the `default:` list once (CR-2026-027).
            return mergeMatch(inline, decodeFields((List<Field>) fallbackFields, ctx));
        }
        if (fallback == null || !"skip".equals(String.valueOf(fallback))) {
            throw new SchemaException.DecodeException(
                    "No matching case for value "
                    + (matchValue != null ? matchValue : discriminator));
        }
        recordMatch(record, field, ctx, null, discriminator);
        return inline;
    }

    /**
     * Whether a discriminator with a fraction lies in a case key. It equals no integer, so
     * no single key and no member of a list matches it; only an inclusive range does
     * (PS-398), as the reference and the spec prototype read {@code lo..hi}.
     */
    static boolean fractionInRange(double value, Object caseVal) {
        if (caseVal instanceof String text) {
            if (parseListCaseKey(text) != null) return false;
            RangeKey range = RangeKey.parse(text);
            return range != null && range.contains(value);
        }
        if (caseVal instanceof Map<?, ?> rangeMap) {
            Object min = rangeMap.get("min"), max = rangeMap.get("max");
            return (!(min instanceof Number lo) || value >= lo.doubleValue())
                    && (!(max instanceof Number hi) || value <= hi.doubleValue());
        }
        return false;
    }

    /**
     * Whether a discriminator satisfies one case key, matching what the Python
     * interpreter's {@code _match_case_pattern} accepts.
     *
     * <p>The string range spelling {@code "2..5"} was missing: only numbers, lists and
     * {@code {min,max}} maps were compared, so a range key matched nothing at all and the
     * construct decoded silently empty (CR-2026-020).
     */
    private boolean matchesCase(long matchValue, Object caseVal) {
        if (caseVal instanceof Number number) {
            Long key = asLong(number);
            return key != null && key == matchValue;
        }
        if (caseVal instanceof String text && parseListCaseKey(text) != null) {
            // A quoted flow sequence, "[1, 2, 3]", matches any element (PS-398).
            return parseListCaseKey(text).contains(matchValue);
        }
        if (caseVal instanceof String text) {
            if (text.contains("..")) {
                RangeKey range = RangeKey.parse(text);
                return range != null && range.contains(matchValue);
            }
            try {
                return matchValue == Long.parseLong(text.trim());
            } catch (NumberFormatException ignored) {
                return false;
            }
        }
        if (caseVal instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Number number && asLong(number) != null
                        && asLong(number) == matchValue) {
                    return true;
                }
            }
            return false;
        }
        if (caseVal instanceof Map<?, ?> rangeMap) {
            long minVal = toLong(rangeMap.get("min"), Long.MIN_VALUE);
            long maxVal = toLong(rangeMap.get("max"), Long.MAX_VALUE);
            return matchValue >= minVal && matchValue <= maxVal;
        }
        return false;
    }

    /**
     * Folds a case's fields onto the discriminator this construct reported, so {@code
     * name} survives whichever branch decoded.
     */
    @SuppressWarnings("unchecked")
    private static Object mergeMatch(Map<String, Object> inline, Object decoded) {
        if (inline == null) {
            return decoded;
        }
        if (decoded instanceof Map<?, ?> decodedMap) {
            inline.putAll((Map<String, Object>) decodedMap);
        }
        return inline;
    }

    /**
     * PS-471: the loop stops {@code reserve} bytes before the region ends, and the fields
     * after the tlv decode from them. The region is narrowed while the loop runs, so every
     * entry is read inside it.
     */
    private Map<String, Object> decodeTLV(Field field, DecodeContext ctx) {
        int reserve = field.getReserve();
        if (reserve <= 0) return decodeTLVEntries(field, ctx);
        if (ctx.remaining() < reserve) {
            throw new SchemaException.DecodeException(String.format(
                    "tlv reserves %d byte(s) but %d remain at offset %d (PS-471)",
                    reserve, ctx.remaining(), ctx.getOffset()));
        }
        int limit = ctx.getLimit();
        ctx.setLimit(limit - reserve);
        try {
            return decodeTLVEntries(field, ctx);
        } finally {
            ctx.setLimit(limit);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> decodeTLVEntries(Field field, DecodeContext ctx) {
        int tagSize = field.getTagSize() > 0 ? field.getTagSize() : 1;
        int lengthSize = field.getLengthSize();
        boolean merge = field.getMerge() == null || field.getMerge();
        String unknownMode = field.getUnknown() != null ? field.getUnknown() : "skip";
        
        Map<String, Object> result = new LinkedHashMap<>();
        List<Map<String, Object>> channels = new ArrayList<>();
        Set<String> collected = new HashSet<>();       // names turned into arrays (PS-157)
        
        // A composite tag's width is its tag_fields' summed widths.
        int tagWidth = tagSize;
        if (field.getTagFields() != null && !field.getTagFields().isEmpty()) {
            tagWidth = 0;
            for (Field tf : field.getTagFields()) {
                // A bit-range component advances by its `consume` alone (PS-060).
                tagWidth += tf.getType() == FieldType.BITS ? tf.getConsume() : tagFieldWidth(tf);
            }
        }

        int entryIndex = -1;            // PS-572: each entry's index, in payload order
        while (ctx.remaining() > 0) {
            int entryStart = ctx.getOffset();
            entryIndex++;
            // PS-477: a tag is never partly read. Fewer bytes than the tag at the start of
            // an entry is an error identifying the tlv, not the end of the loop.
            if (ctx.remaining() < tagWidth) {
                throw new SchemaException.DecodeException(String.format(
                        "tlv entry at offset %d: %d byte(s) remain, fewer than its %d-byte tag (PS-477)",
                        entryStart, ctx.remaining(), tagWidth));
            }
            List<Integer> tag = new ArrayList<>();
            Map<String, Integer> tagValues = new HashMap<>();
            
            if (field.getTagFields() != null && !field.getTagFields().isEmpty()) {
                for (Field tf : field.getTagFields()) {
                    int val;
                    if (tf.getType() == FieldType.BITS) {
                        // A bit range reads its base where it stands and advances by its
                        // `consume` alone (PS-060), as anywhere else; it was read as a
                        // whole byte and consumed one (comtac lpn-td1's `[u8, u8[0:3]]`).
                        int baseBytes = Math.max(1, tf.getBitBaseBytes());
                        byte[] peeked = ctx.peek(baseBytes, 0);
                        String tfEndian = tf.getEndian() != null ? tf.getEndian() : ctx.getEndian();
                        long base = ctx.decodeUnsigned(peeked, tfEndian);
                        val = (int) extractRange(base, tf.isSignedBits(), tf.getBitOffset(),
                                tf.getBits() > 0 ? tf.getBits() : 1);
                        if (tf.getConsume() > 0) {
                            ctx.read(tf.getConsume());
                        }
                    } else {
                        byte[] data = ctx.read(tagFieldWidth(tf));
                        val = (int) ctx.decodeUnsigned(data, ctx.getEndian());
                    }
                    // A component is a field decoded earlier in the payload, so its name
                    // is a reference the case body may make (`ref: $rsn`, PS-265), and its
                    // `var:` binds for the case body, as a field's does; a name_from
                    // template such as "temperature_${ch}" reads it. PS-523 (CR-2026-132):
                    // both bind as any field's do - through its modifiers, and a lookup's
                    // number under a text label, the label kept for templates.
                    // applyGroupMember binds the `var`. The tag is matched on `val`.
                    Object bound = applyGroupMember((long) val, tf, ctx);
                    if (tf.getName() != null) {
                        tagValues.put(tf.getName(), val);
                        if (bound == OMITTED) {
                            ctx.bindMissed(tf.getName(), tf);
                        } else {
                            ctx.bind(tf.getName(), tf, bound);
                        }
                    }
                }
                
                Object tagKey = field.getTagKey();
                if (tagKey instanceof List<?> keyList) {
                    for (Object k : keyList) {
                        if (k instanceof String && tagValues.containsKey(k)) {
                            tag.add(tagValues.get(k));
                        }
                    }
                } else if (tagKey instanceof String) {
                    tag.add(tagValues.getOrDefault((String) tagKey, 0));
                } else if (!field.getTagFields().isEmpty() && field.getTagFields().get(0).getName() != null) {
                    tag.add(tagValues.getOrDefault(field.getTagFields().get(0).getName(), 0));
                }
            } else {
                byte[] data = ctx.read(tagSize);
                tag.add((int) ctx.decodeUnsigned(data, ctx.getEndian()));
            }
            
            int dataLength = -1;
            if (lengthSize > 0) {
                // PS-486: an entry is never partly read. A length cut short is an error,
                // not leftover bytes for PS-472: the schema describes them.
                if (ctx.remaining() < lengthSize) {
                    throw new SchemaException.DecodeException(String.format(
                            "tlv entry at offset %d: %d byte(s) remain after its tag, fewer than its %d-byte length (PS-486)",
                            entryStart, ctx.remaining(), lengthSize));
                }
                byte[] data = ctx.read(lengthSize);
                dataLength = (int) ctx.decodeUnsigned(data, ctx.getEndian());
                // Nor may its value run past the end (the context already stops at a
                // `reserve`), known tag or skipped: a known one read what was there and
                // reported success, a skipped one failed with a bare underflow.
                if (dataLength > ctx.remaining()) {
                    throw new SchemaException.DecodeException(String.format(
                            "tlv entry at offset %d: its length declares %d byte(s), %d remain (PS-486)",
                            entryStart, dataLength, ctx.remaining()));
                }
            }
            
            String caseKey = findTLVCaseKey(field.getTlvCases(), tag);
            
            if (caseKey != null) {
                List<Field> caseFields = field.getTlvCases().get(caseKey);
                DecodeRecord rec = ctx.getRecord();
                boolean pushed = rec != null && rec.push(field.getRaw(), "tlv", entryIndex);
                Map<String, Object> caseResult;
                try {
                    caseResult = decodeFields(caseFields, ctx);
                } finally {
                    if (rec != null) rec.pop(pushed);
                }
                
                if (merge) {
                    OnRepeat.Modes modes = field.getRepeatModes();
                    for (Map.Entry<String, Object> entry : caseResult.entrySet()) {
                        String k = entry.getKey();
                        Object v = entry.getValue();
                        String mode = modes.mode(k);
                        if (!result.containsKey(k)) {
                            result.put(k, v);
                        } else if (!"collect".equals(mode)) {               // PS-525
                            result.put(k, OnRepeat.combine(k, result.get(k), v, mode, modes.last(k)));
                        } else if (collected.contains(k)) {                 // PS-157
                            ((List<Object>) result.get(k)).add(v);
                        } else {
                            // A name reported again becomes an array. Whether it already is
                            // one is not the test: a value that is itself a list (a repeat)
                            // was appended into, splicing its second report among the
                            // first's elements.
                            List<Object> arr = new ArrayList<>();
                            arr.add(result.get(k));
                            arr.add(v);
                            result.put(k, arr);
                            collected.add(k);
                        }
                    }
                } else {
                    Map<String, Object> entry = new LinkedHashMap<>();
                    entry.put("tag", tag);
                    entry.putAll(caseResult);
                    channels.add(entry);
                }
            } else {
                // A tag the schema does not describe. Whatever the mode, the fact is
                // reported: silence cannot be told from a device that sent fewer fields
                // (PS-301, PS-302).
                StringBuilder label = new StringBuilder();
                for (Integer part : tag) {
                    if (label.length() > 0) {
                        label.append(", ");
                    }
                    label.append(String.format("0x%02X", part));
                }

                if ("error".equals(unknownMode)) {
                    throw new SchemaException.DecodeException("Unknown TLV tag: " + label);
                } else if ("raw".equals(unknownMode)) {
                    int span = dataLength >= 0 ? dataLength : ctx.remaining();
                    byte[] raw = ctx.read(span);
                    Map<String, Object> entry = new LinkedHashMap<>();
                    entry.put("tag", tag);
                    entry.put("raw", bytesToHex(raw));
                    // PS-303: reported either way. Merged output has no channel list, so
                    // it goes under `unknown_tags`.
                    if (merge) {
                        Object existing = result.get("unknown_tags");
                        if (existing instanceof List) {
                            ((List<Object>) existing).add(entry);
                        } else {
                            List<Object> list = new ArrayList<>();
                            list.add(entry);
                            result.put("unknown_tags", list);
                        }
                    } else {
                        channels.add(entry);
                    }
                    if (dataLength < 0) {
                        ctx.addWarning(String.format(
                            "unknown TLV tag (%s) captured raw; %d byte(s) after it could not be delimited",
                            label, span));
                        break;
                    }
                } else if (dataLength >= 0) {
                    // skip, the default
                    ctx.addWarning(String.format(
                        "unknown TLV tag (%s) skipped, %d byte(s) discarded", label, dataLength));
                    ctx.read(dataLength);
                } else {
                    // Nothing to skip over, so decoding stops and everything from the tag
                    // onwards is lost (PS-302).
                    ctx.addWarning(String.format(
                        "unknown TLV tag (%s) at offset %d: %d of %d byte(s) left undecoded",
                        label, entryStart, ctx.getLimit() - entryStart,
                        ctx.getLimit()));
                    // This warning is PS-472's for these bytes; no second one is reported.
                    ctx.setLeftoverReported();
                    break;
                }
            }
        }
        
        if (!merge) {
            result.put("channels", channels);
        }
        
        return result;
    }

    private String findTLVCaseKey(Map<String, List<Field>> cases, List<Integer> tag) {
        if (cases == null) return null;
        
        if (tag.size() == 1) {
            String key = String.valueOf(tag.get(0));
            if (cases.containsKey(key)) {
                return key;
            }
        }
        
        String tagJson = tag.toString();
        if (cases.containsKey(tagJson)) {
            return tagJson;
        }

        // Compare composite keys numerically so that spacing does not matter, and
        // so a key may exclude a value with `!` or ignore a tag field with `*`.
        // Exact keys first, then negated, then wildcard (PS-270).
        for (int wanted = 0; wanted <= 2; wanted++) {
            for (String candidate : cases.keySet()) {
                int specificity = matchCompositeCaseKey(candidate, tag);
                if (specificity == wanted) {
                    return candidate;
                }
            }
        }

        return null;
    }

    /** Sentinel for a field that produced no value and is left out of the output. */
    private static final Object OMITTED = new Object();
    /**
     * Omitted as no reading, returned only to a field list (fieldRules): a sentinel
     * (PS-427) and {@code out_of_range: omit} (PS-428). Distinct from OMITTED because
     * the field list records them in {@code _quality} where it is produced.
     */
    private static final Object SENTINEL_ABSENT = new Object();
    private static final Object RANGE_ABSENT = new Object();

    private static final java.util.regex.Pattern NAME_FROM_PATTERN =
            java.util.regex.Pattern.compile("\\$\\{(\\w+)\\}");

    /** Resolves a field's output key, honouring name_from (PS-265, PS-266). */
    private String resolveFieldName(Field field, DecodeContext ctx) {
        String template = field.getNameFrom();
        if (template == null || template.isEmpty()) {
            return field.getName();
        }
        java.util.regex.Matcher matcher = NAME_FROM_PATTERN.matcher(template);
        StringBuilder resolved = new StringBuilder();
        List<String> missing = new ArrayList<>();
        while (matcher.find()) {
            String reference = matcher.group(1);
            // A template writes the value reported, the label where there is a lookup
            // (PS-532), not what a reference binds (PS-523).
            Object value = ctx.reported(reference);
            if (value == DecodeContext.NO_LABEL) {
                throw new SchemaException("name_from for '" + field.getName() + "' references "
                        + reference + ", which reported no value (PS-266, PS-532)");
            }
            String replacement;
            if (value == null) {
                missing.add(reference);
                replacement = "";
            } else {
                replacement = Wave5.renderTemplateValue(value);
                if (replacement == null) {
                    throw new SchemaException(Wave5.templateShapeError(template, reference));
                }
            }
            matcher.appendReplacement(resolved, java.util.regex.Matcher.quoteReplacement(replacement));
        }
        matcher.appendTail(resolved);
        if (!missing.isEmpty()) {
            throw new SchemaException("name_from for '" + field.getName() + "' references "
                    + String.join(", ", missing) + ", which has not been decoded");
        }
        return resolved.toString();
    }

    /**
     * Matches a composite TLV case key against a tag, allowing `!value` to exclude
     * and `*` to ignore a tag field. Returns 0 for an exact match, 1 when any
     * element is negated, 2 when any is a wildcard, and -1 for no match.
     */
    private int matchCompositeCaseKey(String key, List<Integer> tag) {
        String trimmed = key.trim();
        if (!trimmed.startsWith("[")) {
            return -1;
        }
        trimmed = trimmed.substring(1);
        if (trimmed.endsWith("]")) {
            trimmed = trimmed.substring(0, trimmed.length() - 1);
        }
        String[] parts = trimmed.split(",");
        if (parts.length != tag.size()) {
            return -1;
        }
        int specificity = 0;
        for (int i = 0; i < parts.length; i++) {
            String part = parts[i].trim().replaceAll("^[\"']|[\"']$", "");
            if ("*".equals(part)) {
                specificity = Math.max(specificity, 2);
                continue;
            }
            boolean negated = part.startsWith("!");
            String text = negated ? part.substring(1).trim() : part;
            int expected;
            try {
                expected = text.startsWith("0x") || text.startsWith("0X")
                        ? Integer.parseInt(text.substring(2), 16)
                        : Integer.parseInt(text);
            } catch (NumberFormatException e) {
                return -1;
            }
            if (negated) {
                specificity = Math.max(specificity, 1);
                if (tag.get(i) == expected) {
                    return -1;
                }
            } else if (tag.get(i) != expected) {
                return -1;
            }
        }
        return specificity;
    }

    /**
     * Decode a repeat (count, byte_length, or until: end less any reserve).
     *
     * <p>Each element goes through the same field-list decoder as the top level, in a scope
     * of its own: its names resolve within the element and are gone after it (PS-368). The
     * index is bound while each is decoded (PS-366), present_if drops an element after it is
     * decoded (PS-386), a carried field sees its own previous value (PS-378 to PS-380), and
     * count_as is bound to the number reported once the array is complete (PS-367,
     * CR-2026-080). Mirrors _decode_repeat in tools/schema_interpreter.py.
     */
    private List<Object> decodeRepeat(Field field, DecodeContext ctx) {
        int maxIterations = field.getMax() > 0 ? field.getMax() : 1000;
        int minIterations = field.getMin();

        List<Object> result = new ArrayList<>();
        int[] iterations = {0};

        // PS-378, PS-380: a carried field starts again from its `carry` value each time
        // the repeat begins, including each iteration of an enclosing repeat.
        Map<String, Object> carryState = new LinkedHashMap<>();
        for (Field nested : field.getFields() == null ? List.<Field>of() : field.getFields()) {
            if (nested.hasCarry()) carryState.put(nested.getName(), carryInitial(nested, ctx));
        }

        if (field.getCount() != null) {
            int count;
            if (field.getCount() instanceof Number) {
                count = ((Number) field.getCount()).intValue();
            } else if (field.getCount() instanceof String) {
                String varName = ((String) field.getCount()).replace("$", "");
                Object val = ctx.getVariable(varName);
                if (val == null) {
                    throw new SchemaException.DecodeException("Repeat count variable not found: " + varName);
                }
                count = toInt(val, 0);
            } else {
                throw new SchemaException.DecodeException("Invalid count type: " + field.getCount().getClass());
            }
            
            // PS-396: more elements than `max` is an error, not a silent truncation. The
            // count was clamped here, so the next field read from inside an element the
            // clamp had discarded.
            if (count > maxIterations) {
                throw repeatLimitError(field, maxIterations, "count " + count,
                        ctx.getOffset(), ctx.getLimit());
            }
            
            for (int i = 0; i < count; i++) {
                decodeElement(field, ctx, iterations, carryState, result);
            }
        } else if (field.getByteLength() != null) {
            int byteLen;
            if (field.getByteLength() instanceof Number) {
                byteLen = ((Number) field.getByteLength()).intValue();
            } else if (field.getByteLength() instanceof String) {
                String varName = ((String) field.getByteLength()).replace("$", "");
                Object val = ctx.getVariable(varName);
                if (val == null) {
                    throw new SchemaException.DecodeException("Repeat byte_length variable not found: " + varName);
                }
                byteLen = toInt(val, 0);
            } else {
                throw new SchemaException.DecodeException("Invalid byte_length type");
            }
            
            int endOffset = ctx.getOffset() + byteLen;

            while (ctx.getOffset() < endOffset && iterations[0] < maxIterations) {
                decodeElement(field, ctx, iterations, carryState, result);
            }
            
            if (ctx.getOffset() != endOffset) {
                // PS-088: the members must divide the span exactly. The message used to
                // blame the payload for both ways they do not - "expected end at 4, got
                // 2" reads as a short payload even where the schema's own ceiling stopped
                // the loop with bytes to spare (CR-2026-022).
                if (iterations[0] >= maxIterations && ctx.getOffset() < endOffset) {
                    throw repeatLimitError(field, maxIterations, "byte_length " + byteLen,
                            ctx.getOffset(), endOffset);
                }
                throw new SchemaException.DecodeException(
                    String.format("Repeat byte_length mismatch: expected end at %d, got %d", endOffset, ctx.getOffset()));
            }
        } else if ("end".equals(field.getUntil())) {
            // PS-343 to PS-344a: a tail too short for a whole element is an error naming
            // the repeat as a ragged tail, tested before the element begins where its
            // size is fixed. This began the element and failed part-way with the
            // underflow of whichever member ran out.
            //
            // PS-350, PS-351: the region ends `reserve` bytes before the payload does,
            // and the ragged-tail rule applies to that region. Its elements decode with
            // the region narrowed to it, so nothing inside one can read the trailer.
            int reserve = field.getReserve();
            int payloadEnd = ctx.getLimit();
            int regionEnd = payloadEnd - reserve;
            if (regionEnd < ctx.getOffset()) {
                throw new SchemaException.DecodeException(String.format(
                        "repeat '%s' reserves %d byte(s) but %d remain at offset %d (PS-351)",
                        field.getName(), reserve, ctx.remaining(), ctx.getOffset()));
            }
            int elementSize = fixedElementSize(field.getFields());
            ctx.setLimit(regionEnd);
            try {
                while (ctx.remaining() > 0 && iterations[0] < maxIterations) {
                    int start = ctx.getOffset();
                    if (elementSize > 0 && ctx.remaining() < elementSize) {
                        throw raggedTailError(field, ctx.remaining(), elementSize, start);
                    }
                    try {
                        decodeElement(field, ctx, iterations, carryState, result);
                    } catch (SchemaException.BufferUnderflow e) {
                        // PS-343: a short element of an until-end repeat is its ragged
                        // tail, which names the repeat (as PS-535 permits).
                        throw raggedTailError(field, regionEnd - start, 0, start);
                    }
                }
                if (iterations[0] >= maxIterations && ctx.remaining() > 0) {
                    throw repeatLimitError(field, maxIterations, "until: end",
                            ctx.getOffset(), regionEnd);
                }
            } finally {
                ctx.setLimit(payloadEnd);
            }
            // PS-383: a trailer was decoded from the reserved bytes before the first
            // element, so nothing after the repeat reads them again.
            if (field.getTrailer() != null && !field.getTrailer().isEmpty()) {
                ctx.setOffset(payloadEnd);
            }
        } else {
            throw new SchemaException.DecodeException("Repeat field must specify one of: count, byte_length, or until");
        }

        if (result.size() < minIterations) {
            throw new SchemaException.DecodeException(
                String.format("Repeat produced %d elements, but minimum is %d", result.size(), minIterations));
        }

        // PS-546: a sibling identifying these values position by position reported as
        // many values as this repeat, or the pairing names nothing.
        if (field.getRaw() != null) {
            Set<String> seen = new LinkedHashSet<>();
            for (String[] ref : Wave6a.siblingReferences(field.getRaw())) {
                if (!seen.add(ref[1])) continue;
                Object reported = ctx.getVariable(ref[1]);
                if (!(reported instanceof List<?> l) || l.size() != result.size()) {
                    Object have = reported instanceof List<?> l2 ? (Object) l2.size() : "none";
                    throw new SchemaException.DecodeException("Repeat '" + field.getName() + "' has "
                            + result.size() + " values and '" + ref[1] + "', which identifies them, has "
                            + have + " (PS-546)");
                }
            }
        }

        // PS-367: the number of elements reported, bound for every later field only.
        if (field.getCountAs() != null) {
            ctx.setVariable(field.getCountAs(), (long) result.size());
        }

        return result;
    }

    /**
     * Decode one element in a scope of its own, and append it unless present_if drops it.
     * The iteration count advances either way (PS-386).
     */
    private void decodeElement(Field repeat, DecodeContext ctx, int[] iterations,
                               Map<String, Object> carryState, List<Object> result) {
        Map<String, Object> outer = ctx.snapshotVariables();
        Map<String, Object> outerLabels = ctx.snapshotLabels();
        Set<String> savedNames = ctx.getElementNames();
        List<Field> members = repeat.getFields() == null ? List.of() : repeat.getFields();
        Set<String> names = new HashSet<>();
        for (Field member : members) names.add(member.getName());
        ctx.setElementNames(names);
        if (repeat.getIndex() != null) {
            // PS-366, PS-530: the position counted from index_base.
            ctx.setVariable(repeat.getIndex(), (long) iterations[0] + repeat.getIndexBase());
        }
        ctx.getVariables().putAll(carryState);                              // PS-379
        Map<String, Object> element;
        boolean keep;
        // PS-541: the records this element's `records` entry yields, if it reaches one.
        List<Map<String, Object>> savedRecords = ctx.getPendingRecords();
        List<Map<String, Object>> records = new ArrayList<>();
        ctx.setPendingRecords(records);
        DecodeRecord rec = ctx.getRecord();
        // PS-572: the element's position, in the locations of what it decodes.
        boolean pushed = rec != null && rec.push(repeat.getRaw(), "repeat", iterations[0]);
        try {
            element = decodeFields(members, ctx);
            for (String carried : carryState.keySet()) {
                if (ctx.getVariables().containsKey(carried)) {
                    carryState.put(carried, ctx.getVariable(carried));
                }
            }
            // The element is whole here, so a name of it with no value is absent, not
            // "not yet decoded", and the condition fails (PS-540).
            keep = repeat.getPresentIf() == null || guardPasses(repeat.getPresentIf(), ctx, true);
        } finally {
            // PS-368: an element's names do not outlive it.
            ctx.restoreVariables(outer);
            ctx.restoreLabels(outerLabels);
            ctx.setElementNames(savedNames);
            ctx.setPendingRecords(savedRecords);
            if (rec != null) rec.pop(pushed);
        }
        iterations[0]++;
        if (keep && !records.isEmpty()) {
            // PS-541: the records, as consecutive elements, in place of the element; the
            // element's other reported fields go into each, before its own.
            for (Map<String, Object> record : records) {
                Map<String, Object> merged = new LinkedHashMap<>(element);
                merged.putAll(record);
                result.add(merged);
            }
        } else if (keep) {                                                  // PS-386
            // PS-528: an element repeat reports the value, or null where it was omitted.
            result.add(repeat.getElementName() == null ? element : elementValue(element));
        }
    }

    /**
     * The value a repeat of single values reports for one element: the record's one
     * reported value, or null where it was omitted (PS-528), so positions keep their index.
     * Interpreter metadata beside it is not the value. Mirrors element_value.
     */
    static Object elementValue(Map<String, Object> record) {
        for (Map.Entry<String, Object> e : record.entrySet()) {
            if (!"_quality".equals(e.getKey()) && !"_warnings".equals(e.getKey())
                    && !"_meta".equals(e.getKey())) {
                return e.getValue();
            }
        }
        return null;
    }

    /** A carried field's value before the first element (PS-378). */
    private static Object carryInitial(Field field, DecodeContext ctx) {
        Object carry = field.getCarry();
        if (carry instanceof String text && text.startsWith("$")) {
            String name = text.substring(1);
            if (!ctx.getVariables().containsKey(name)) {
                throw new SchemaException.DecodeException("carry of '" + field.getName() + "' names "
                        + text + ", which was not decoded before the repeat (PS-381)");
            }
            return ctx.getVariable(name);
        }
        return carry;
    }

    private String decodeBitfieldString(long intVal, Field field) {
        String delimiter = field.getDelimiter() != null ? field.getDelimiter() : ".";
        String prefix = field.getPrefix() != null ? field.getPrefix() : "";
        
        List<String> partStrs = new ArrayList<>();
        if (field.getParts() != null) {
            for (List<Object> part : field.getParts()) {
                if (part.size() < 2) continue;
                
                int bitOff = toInt(part.get(0), 0);
                int bitLen = toInt(part.get(1), 0);
                Object format = part.size() >= 3 ? part.get(2) : "decimal";
                
                // The value is unsigned: a logical shift, and a 64-bit part's mask is all
                // ones - Java takes a shift count mod 64, so `(1L << 64) - 1` was 0 and
                // a 64-bit part read 0.
                long mask = bitLen >= 64 ? -1L : (1L << bitLen) - 1;
                long raw = bitOff >= 64 ? 0 : (intVal >>> bitOff) & mask;
                
                // PS-430, PS-582: the part's format writes it (checked at load).
                partStrs.add(BitfieldParts.format(raw, bitLen, format));
            }
        }
        
        return prefix + String.join(delimiter, partStrs);
    }

    /**
     * What a non-consuming byte string decodes from {@code span} (PS-589, CR-2026-175):
     * its format and separator, or PS-534's trailing 0x00 removal, as a decoder applies them.
     */
    Object echoValue(Field field, byte[] span) {
        switch (field.getType()) {
            case BYTES: return formatBytes(span, field.getFormat(), field.getSeparator());
            case HEX: return bytesToHex(span);
            default: {
                String str = new String(span, StandardCharsets.ISO_8859_1);
                int end = str.length();
                while (end > 0 && str.charAt(end - 1) == '\0') end--;
                Object value = str.substring(0, end);
                if (field.getNumeral() != null) {
                    value = Numeral.decode(field.getName(), (String) value, field.getNumeral());
                }
                return value;
            }
        }
    }

    private Object formatBytes(byte[] data, String format, String separator) {
        if (format == null) format = "hex";
        
        return switch (format) {
            case "hex", "hex:lower" -> {
                if (separator != null && !separator.isEmpty()) {
                    StringBuilder sb = new StringBuilder();
                    for (int i = 0; i < data.length; i++) {
                        if (i > 0) sb.append(separator);
                        sb.append(String.format("%02x", data[i]));
                    }
                    yield sb.toString();
                }
                yield bytesToHex(data);
            }
            case "hex:upper" -> {
                if (separator != null && !separator.isEmpty()) {
                    StringBuilder sb = new StringBuilder();
                    for (int i = 0; i < data.length; i++) {
                        if (i > 0) sb.append(separator);
                        sb.append(String.format("%02X", data[i]));
                    }
                    yield sb.toString();
                }
                yield bytesToHex(data).toUpperCase();
            }
            case "base64" -> Base64.getEncoder().encodeToString(data);
            case "array" -> {
                List<Integer> arr = new ArrayList<>();
                for (byte b : data) {
                    arr.add(b & 0xFF);
                }
                yield arr;
            }
            default -> bytesToHex(data);
        };
    }

    // Utility methods
    private static String bytesToHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    /**
     * Decode a byte_group: several fields packed into the same byte or bytes.
     *
     * <p>Each member reads from the group's starting position and consumes nothing of
     * its own; the cursor advances once, by the group's size, when they are all done.
     * Members are emitted flat alongside their siblings, and recorded as variables so
     * later computed fields can reference them. A name beginning with an underscore is
     * internal: it becomes a variable but is not reported.
     */
    private Map<String, Object> decodeByteGroup(Field group, DecodeContext ctx) {
        Map<String, Object> result = new LinkedHashMap<>();
        int start = ctx.getOffset();
        // PS-364: the group's bytes are one value in its effective byte order, and a
        // member's bit positions refer to that value. Each member read its own base from
        // the group's start, big-endian, which agrees only for a big-endian group whose
        // members are as wide as it is.
        String endian = group.getGroupEndian() != null ? group.getGroupEndian() : this.endian;
        if (group.getByteGroupSize() > ctx.remaining()) {
            // PS-535: the group is read as one value (CR-2026-131), so the failing read is
            // the group's, in the peek form, and every member is lost; it is identified by
            // its members, as the spec prototype does.
            List<String> memberNames = new ArrayList<>();
            for (Field member : group.getByteGroup()) memberNames.add(member.getName());
            throw new SchemaException.BufferUnderflow(String.format(
                    "Buffer underflow at peek offset %d", start))
                    .namedAs(SchemaException.BufferUnderflow.byteGroupLabel(memberNames));
        }
        long groupValue = ctx.decodeUnsigned(ctx.peek(group.getByteGroupSize(), 0), endian);

        for (Field member : group.getByteGroup()) {
            ctx.setOffset(start);
            String name = member.getName();
            String memberVar = member.getVar();
            boolean hasName = name != null && !name.isEmpty();
            boolean hasVar = memberVar != null && !memberVar.isEmpty();
            if (!hasName && !hasVar) continue;
            if (hasName) ctx.produced(member, null);                // PS-490
            try {
                Object value;
                if (member.getType() == FieldType.BITS) {
                    value = applyGroupMember(extractRange(groupValue, member.isSignedBits(),
                            member.getBitOffset(), member.getBits()), member, ctx);
                } else if (member.getType() == FieldType.BOOL) {
                    int bit = Math.max(0, member.getBoolBit());
                    value = ((groupValue >>> bit) & 1) == 1;
                } else {
                    // PS-070: a whole integer is its full range of the group's value, read
                    // from the group's start. It was read as a field of its own.
                    value = applyGroupMember(extractRange(groupValue, member.getType().isSigned(),
                            0, member.getType().defaultLength() * 8), member, ctx);
                }
                if (value == OMITTED) {
                    if (hasName) ctx.bindMissed(name, member);      // PS-523
                    if (hasVar) ctx.bindMissed(memberVar, member);  // and under its var
                    continue;
                }
                if (value == null) continue;
                if (hasName) ctx.bind(name, member, value);         // PS-523
                if (hasVar) ctx.bind(memberVar, member, value);     // and under its var
                if (!hasName) continue;
                if (name.startsWith("_")) {
                    // PS-572: as the reference records a group member - a bool its flag,
                    // any other the number its name binds.
                    recordInternal(ctx, member.getRaw(),
                            member.getType() == FieldType.BOOL ? value : ctx.getVariable(name));
                }
                if (!name.startsWith("_")) {
                    String outputName = resolveFieldName(member, ctx);
                    if (!outputName.equals(name)) ctx.produced(member, outputName);   // PS-492
                    result.put(outputName, value);
                }
            } catch (RuntimeException e) {
                // One unreadable member must not abandon the rest of the payload.
                continue;
            }
        }

        ctx.setOffset(start + group.getByteGroupSize());
        return result;
    }

    /**
     * Parse map-shaped match cases, {@code cases: {0x00: [...], default: [...]}}.
     * SnakeYAML resolves a {@code 0x00} key to an Integer, so the case value arrives
     * already numeric and needs no hex handling of its own.
     */
    @SuppressWarnings("unchecked")
    private static List<Field.Case> parseCaseMap(Object casesRaw) {
        List<Field.Case> cases = new ArrayList<>();
        if (!(casesRaw instanceof Map<?, ?> casesMap)) return cases;
        for (Map.Entry<?, ?> entry : casesMap.entrySet()) {
            if (!(entry.getValue() instanceof List<?> caseFields)) continue;
            Field.Case c = new Field.Case();
            if ("default".equals(String.valueOf(entry.getKey()))) {
                c.setDefault(true);
            } else {
                c.setCaseValue(entry.getKey());
            }
            c.setFields(parseFields((List<Map<String, Object>>) caseFields));
            cases.add(c);
        }
        // A default must be tried only after every explicit case, whatever order the
        // document lists them in; decodeMatch returns on the first match it sees.
        cases.sort(Comparator.comparing(Field.Case::isDefault));
        return cases;
    }

    /** The comparison operators a guard condition may carry, in precedence order. */
    private static final List<String> GUARD_OPS =
            List.of("gt", "gte", "lt", "lte", "eq", "ne", "in", "not_in");     // PS-543

    private static Field.Guard parseGuard(Map<?, ?> guardRaw) {
        Field.Guard guard = new Field.Guard();
        if (guardRaw.containsKey("else")) {
            guard.setHasElse(true);
            guard.setElseValue(guardRaw.get("else"));
        }
        if (guardRaw.get("when") instanceof List<?> conditions) {
            for (Object conditionRaw : conditions) {
                if (!(conditionRaw instanceof Map<?, ?> cm)) continue;
                Object fieldRef = cm.get("field");
                if (!(fieldRef instanceof String reference)) continue;
                for (String op : GUARD_OPS) {
                    if (!cm.containsKey(op)) continue;
                    Field.Condition condition = new Field.Condition();
                    condition.setField(reference);
                    condition.setOp(op);
                    if (cm.get(op) instanceof List<?> values) {                  // PS-543
                        List<Double> operands = new ArrayList<>();
                        for (Object v : values) operands.add(toDouble(v));
                        condition.setOperands(operands);
                    } else {
                        condition.setOperand(toDouble(cm.get(op)));
                    }
                    guard.getWhen().add(condition);
                    break;
                }
            }
        }
        return guard;
    }

    /**
     * Resolve a computed field (type: number): ref, polynomial, compute, guard.
     *
     * <p>This applies the field's own arithmetic, because the order differs from an
     * ordinary field's: a ref runs polynomial, then modifiers, then transform, while a
     * compute runs transform alone. A guard's fallback is reported exactly as declared.
     */
    /**
     * Brings one decoded value to its reported JSON representation (CR-2026-008).
     *
     * <p>PS-279 makes the declared type decide the reported type, and PS-280 makes an
     * integral value serialize without a fraction. This binding widened every integer:
     * {@code applyArithmetic} takes and returns a double and was called for any numeric
     * field, so a plain {@code u8} came back as {@code Double} 5.0 and Jackson wrote it
     * as {@code 5.0} where the interpreters and every deployed JS codec write {@code 5}.
     * Narrowing here rather than skipping the arithmetic also covers a scaled field
     * whose reading happens to be whole.
     *
     * <p>PS-282: NaN and the infinities are not JSON values, so a value holding one is
     * reported absent - returns null.
     */
    private static Object normalizeOutput(Object value) {
        if (value instanceof Double || value instanceof Float) {
            double d = ((Number) value).doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) {
                return null;
            }
            if (d == Math.rint(d) && Math.abs(d) <= 9.007199254740992E15) {
                return (long) d;
            }
            return d;
        }
        if (value instanceof byte[] raw) {
            // PS-281: a byte sequence reports as a lowercase hex string.
            StringBuilder sb = new StringBuilder(raw.length * 2);
            for (byte b : raw) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        }
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : map.entrySet()) {
                Object v = normalizeOutput(e.getValue());
                if (v != null) {
                    out.put(String.valueOf(e.getKey()), v);
                }
            }
            return out;
        }
        if (value instanceof List<?> list) {
            List<Object> out = new ArrayList<>(list.size());
            for (Object item : list) {
                // PS-528: a null in an array is an `element` repeat's omitted value, and
                // keeps its position; dropping it would move every later value.
                out.add(normalizeOutput(item));
            }
            return out;
        }
        return value;
    }

    private Object decodeComputed(Field field, DecodeContext ctx) {
        boolean computed = field.getRef() != null || field.getCompute() != null;
        if (computed && field.getGuard() != null && !guardPasses(field.getGuard(), ctx, false)) {
            // PS-444: a failing guard reports the declared fallback untouched - no
            // modifiers, no transform, no lookup; the wrapper is what keeps the lookup
            // in applyGroupMember off it. Checking the guard first is also what keeps a
            // guarded division by zero from ever running.
            return field.getGuard().hasElse() ? new GuardElse(field.getGuard().getElseValue()) : Double.NaN;
        }

        // PS-443: the source - the ref value with its polynomial, or the compute result -
        // then the modifiers and the transform stages here, and the lookup after, in
        // applyGroupMember. The compute path ran the stages alone, so `mult: 10` beside a
        // `compute` was dropped with success.
        if (field.getRef() != null) {
            double value = resolveOperand(field.getRef(), ctx);
            // PS-314/PS-429: the referenced field has no value, so this one is absent.
            if (isComputeOmitted(value)) {
                return null;
            }
            if (field.getPolynomial() != null && !field.getPolynomial().isEmpty()) {
                value = evaluatePolynomial(field.getPolynomial(), value);
            }
            return applyArithmetic(value, field);
        }

        if (field.getCompute() != null) {
            double value = evaluateCompute(field.getCompute(), ctx);
            // A zero divisor omits the field (PS-278). Short-circuit before the
            // arithmetic, which would otherwise operate on the sentinel.
            if (isComputeOmitted(value)) {
                return null;
            }
            return applyArithmetic(value, field);
        }

        return field.getValue();
    }

    /** A failed guard's {@code else}, reported exactly as declared (PS-444). */
    private record GuardElse(Object value) { }

    /**
     * Apply a field's bare modifiers and then its transform stages. Both run when both
     * are present - a field may scale with `mult` and then round with a stage - which
     * an either/or chain here used to get wrong, dropping the modifier. The modifiers
     * run in the canonical order mult, div, add, whatever order the keys were written
     * in (PS-101); the stages run in list order.
     */
    static double applyArithmetic(double value, Field field) {
        if (field.getMult() != null) value *= field.getMult();
        if (field.getDiv() != null) {
            // A zero divisor omits the field (PS-100): NaN, which the post-read step
            // turns into an omission. Skipping the division reported the undivided value.
            if (field.getDiv() == 0) return Double.NaN;
            value /= field.getDiv();
        }
        if (field.getAdd() != null) value += field.getAdd();
        return applyTransform(value, field.getTransform());
    }

    private static double applyTransform(double value, List<Field.Transform> stages) {
        if (stages == null) return value;
        for (Field.Transform stage : stages) {
            if ("round".equals(stage.getOp())) {
                int decimals = stage.getDecimals() == null ? 0 : stage.getDecimals();
                // Half-to-even by default, matching the interpreter's rounding; `ties:
                // away` rounds a tie away from zero (PS-390). BigDecimal(double) is the
                // exact binary value, so a tie is recognised only where there is one.
                if (Double.isNaN(value) || Double.isInfinite(value)) continue;
                // `up` is toward +infinity: HALF_UP for a non-negative value, HALF_DOWN
                // (toward zero) for a negative one.
                String ties = stage.getTies();
                java.math.RoundingMode mode = "away".equals(ties)
                        ? java.math.RoundingMode.HALF_UP
                        : "up".equals(ties)
                        ? (value < 0 ? java.math.RoundingMode.HALF_DOWN
                                     : java.math.RoundingMode.HALF_UP)
                        : java.math.RoundingMode.HALF_EVEN;
                value = new java.math.BigDecimal(value).setScale(decimals, mode).doubleValue();
                continue;
            }
            if (stage.getFloor() != null) { value = Math.max(value, stage.getFloor()); continue; }
            if (stage.getCeiling() != null) { value = Math.min(value, stage.getCeiling()); continue; }
            // Unary maths stages, each exclusive of the others and of the arithmetic
            // ops, in the order the Python interpreter checks them. sqrt clamps a
            // negative input at 0 (PS-116). The log of a non-positive number has no
            // value, so the field is absent (PS-117): NaN, which the post-read step
            // omits. The logs clamped at 1e-10 before, reporting log10(0) as -10.
            if (Boolean.TRUE.equals(stage.getSqrt())) {
                value = Math.sqrt(Math.max(0.0, value));
                continue;
            }
            if (Boolean.TRUE.equals(stage.getAbs())) {
                value = Math.abs(value);
                continue;
            }
            if (stage.getPow() != null) {
                value = Math.pow(value, stage.getPow());
                continue;
            }
            if (Boolean.TRUE.equals(stage.getLog10())) {
                if (!(value > 0)) return Double.NaN;
                value = Math.log10(value);
                continue;
            }
            if (Boolean.TRUE.equals(stage.getLog())) {
                if (!(value > 0)) return Double.NaN;
                value = Math.log(value);
                continue;
            }
            if (stage.getMult() != null) value *= stage.getMult();
            if (stage.getDiv() != null) {
                if (stage.getDiv() == 0) return Double.NaN;   // PS-100: absent
                value /= stage.getDiv();
            }
            if (stage.getAdd() != null) value += stage.getAdd();
        }
        return value;
    }

    /** Horner's method over coefficients in descending power order. */
    private static double evaluatePolynomial(List<Double> coefficients, double x) {
        double result = coefficients.get(0) == null ? 0.0 : coefficients.get(0);
        for (int i = 1; i < coefficients.size(); i++) {
            Double coefficient = coefficients.get(i);
            result = result * x + (coefficient == null ? 0.0 : coefficient);
        }
        return result;
    }

    private double evaluateCompute(Field.Compute compute, DecodeContext ctx) {
        double a = resolveOperand(compute.getA(), ctx);
        double b = resolveOperand(compute.getB(), ctx);
        // PS-314/PS-429: an operand with no value makes the field absent, as a zero
        // divisor does. NaN arithmetic is not relied on to carry the sentinel through.
        if (isComputeOmitted(a) || isComputeOmitted(b)) {
            return COMPUTE_OMITTED;
        }
        return switch (compute.getOp()) {
            case "add" -> a + b;
            case "sub" -> a - b;
            case "mul" -> a * b;
            // PS-278: a zero divisor omits the field. NaN is not a JSON value, so
            // returning it made the whole decode unparseable by a conforming consumer.
            case "div" -> b == 0 ? COMPUTE_OMITTED : a / b;
            // PS-277 floored. This was `%`, which truncates, so it gave -1 where the
            // floored answer is 2 - and it sat beside a floorDiv `idiv`, meaning this
            // binding's own two operators used different conventions and
            // a == idiv(a,b)*b + mod(a,b) did not hold. Math.floorMod is the match.
            case "mod" -> b == 0 ? COMPUTE_OMITTED : (double) Math.floorMod((long) a, (long) b);
            // PS-276 floored, already correct.
            case "idiv" -> b == 0 ? COMPUTE_OMITTED : (double) Math.floorDiv((long) a, (long) b);
            // PS-584: a result that is not a finite real number omits the field.
            case "pow" -> Double.isFinite(Math.pow(a, b)) ? Math.pow(a, b) : COMPUTE_OMITTED;
            default -> throw new SchemaException.DecodeException(
                    "Unknown compute op: " + compute.getOp());
        };
    }

    /** Resolve a {@code $field} reference against decoded variables, or a literal. */
    private double resolveOperand(Object spec, DecodeContext ctx) {
        if (spec instanceof String text && text.startsWith("$")) {
            Object value = ctx.ref(text.substring(1));     // PS-368
            // PS-314/PS-429: a reference with no value - a field omitted earlier by a
            // zero divisor, a log of x <= 0, a sentinel or a lookup with no entry - cannot
            // be resolved. It read 0, so dragino wsc2-lb's `rain` was reported as 0
            // where its divisor was 0 and the field should be absent.
            if (value == null) {
                return COMPUTE_OMITTED;
            }
            return value instanceof Number number ? number.doubleValue() : 0.0;
        }
        Double literal = toDouble(spec);
        return literal == null ? 0.0 : literal;
    }

    /**
     * PS-540 (CR-2026-117): a condition whose field is absent (PS-314) or unbound fails, so
     * the guard's {@code else} applies, or the field is omitted without one (PS-400). It was
     * read as 0, which passed {@code gte: 0} on a value the device never sent. The PS-368
     * scope errors stay errors, except for a repeat's {@code present_if}, evaluated once its
     * element is whole ({@code elementComplete}).
     */
    private boolean guardPasses(Field.Guard guard, DecodeContext ctx, boolean elementComplete) {
        for (Field.Condition condition : guard.getWhen()) {
            if (!condition.getField().startsWith("$")) continue;
            String name = condition.getField().substring(1);
            Object raw = elementComplete ? ctx.getVariables().get(name) : ctx.ref(name); // PS-368
            // PS-540: an absent operand fails. PS-549 (CR-2026-127): so does a boolean one -
            // comparisons take numbers, and `true` is not 1 (PS-513).
            if (!(raw instanceof Number number)) return false;
            double value = number.doubleValue();
            double operand = condition.getOperand() == null ? 0.0 : condition.getOperand();
            boolean passed = switch (condition.getOp()) {
                case "gt" -> value > operand;
                case "gte" -> value >= operand;
                case "lt" -> value < operand;
                case "lte" -> value <= operand;
                case "eq" -> value == operand;
                case "ne" -> value != operand;
                case "in" -> condition.getOperands().contains(value);          // PS-543
                case "not_in" -> !condition.getOperands().contains(value);
                default -> true;
            };
            if (!passed) return false;
        }
        return true;
    }

    /**
     * A value as a 64-bit integer, for comparing a discriminator with a case key, or null
     * where it has none. {@code intValue()} cannot do it: a Double above
     * Integer.MAX_VALUE saturates while a Long wraps, so the same number read two ways
     * compared unequal. Integral types convert exactly; a Double truncates as {@code
     * toInt} does; one outside long's range, NaN, or text that is not an integer is null.
     */
    private static Long asLong(Object obj) {
        if (obj instanceof Long || obj instanceof Integer || obj instanceof Short
                || obj instanceof Byte) {
            return ((Number) obj).longValue();
        }
        if (obj instanceof java.math.BigInteger big) {
            return big.bitLength() < 64 ? big.longValue() : null;
        }
        if (obj instanceof Number number) {
            double d = number.doubleValue();
            if (Double.isNaN(d) || d < -9.2233720368547758E18 || d >= 9.2233720368547758E18) {
                return null;
            }
            return (long) d;
        }
        if (obj instanceof String text) {
            try {
                return Long.parseLong(text.trim());
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return null;
    }

    private static long toLong(Object obj, long defaultValue) {
        Long value = asLong(obj);
        return value != null ? value : defaultValue;
    }

    private static int toInt(Object obj, int defaultValue) {
        if (obj == null) return defaultValue;
        if (obj instanceof Number) return ((Number) obj).intValue();
        if (obj instanceof String) {
            try {
                return Integer.parseInt((String) obj);
            } catch (NumberFormatException e) {
                return defaultValue;
            }
        }
        return defaultValue;
    }

    /**
     * A lookup label as the schema wrote it: a String, a Number or a Boolean (PS-106).
     * Anything else - a nested mapping, say - keeps its old rendering as text.
     */
    private static Object lookupLabel(Object obj) {
        if (obj instanceof String || obj instanceof Boolean || obj instanceof Number) return obj;
        return String.valueOf(obj);
    }

    /**
     * PS-513 (CR-2026-104): a boolean label matches only a boolean input, and a boolean
     * input only a boolean label - {@code true} is not 1, and "true" is not {@code true}. A
     * string matches only an equal string, and a number any number equal to it.
     */
    static boolean sameLabel(Object label, Object value) {
        if (label instanceof Boolean || value instanceof Boolean) {
            return label instanceof Boolean && label.equals(value);
        }
        if (label instanceof String || value instanceof String) {
            return label instanceof String && label.equals(value);
        }
        return label instanceof Number l && value instanceof Number v
                && l.doubleValue() == v.doubleValue();
    }

    /** Reads a flag written as a YAML boolean, or as "true"/1 by a JSON producer. */
    private static Boolean toBoolean(Object obj) {
        if (obj == null) return null;
        if (obj instanceof Boolean) return (Boolean) obj;
        if (obj instanceof Number) return ((Number) obj).doubleValue() != 0.0;
        if (obj instanceof String) return Boolean.parseBoolean((String) obj);
        return null;
    }

    private static Double toDouble(Object obj) {
        if (obj == null) return null;
        if (obj instanceof Number) return ((Number) obj).doubleValue();
        if (obj instanceof String) {
            try {
                return Double.parseDouble((String) obj);
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return null;
    }

    public static class PortDef {
        private String direction;
        private String description;
        private List<Field> fields;

        public String getDirection() { return direction; }
        public void setDirection(String direction) { this.direction = direction; }
        
        public String getDescription() { return description; }
        public void setDescription(String description) { this.description = description; }
        
        public List<Field> getFields() { return fields; }
        public void setFields(List<Field> fields) { this.fields = fields; }
    }
}
