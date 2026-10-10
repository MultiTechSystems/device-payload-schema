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
    /** Names declared only inside some repeat's elements (PS-368). */
    private Set<String> repeatOnlyNames = Set.of();
    /** The document's {@code name} as written, null where it has none (`_meta.schema`). */
    private Object metaName;
    /** Whether the document declares {@code version}, and the value it declares. */
    private boolean declaresVersion;
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

    public static Schema fromYaml(String yamlContent) {
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
        Schema schema = new Schema();
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
            checkBytesFormat(fm);
            if (fm.containsKey("byte_group")) checkByteGroupOverlap(fm.get("byte_group"));
            checkLiteral(fm);
            checkWave4(fm);
            Wave5.checkLookupTemplate(fm);     // PS-407
            // PS-399, PS-414, PS-416, PS-427, PS-428: the match's discriminator source,
            // sentinel and out_of_range.
            Wave6b.checkField(fm);
            checkArithmetic(fm);               // PS-445, PS-452
            fields.add(parseField(fm));
        }
        return fields;
    }

    private static final String REF_PREFIX = "#/definitions/";

    /**
     * CR-2026-045: splice every {@code {$ref: '#/definitions/<name>'}} into the field list
     * it sits in, before anything is parsed, so a reference works in any field list - a
     * port entry, an object, a repeat, a case (PS-346, PS-347). This resolved only the
     * schema's and the ports' own lists, and an unresolvable reference was kept and
     * produced nothing. Rejected at load: a definition that is not a field group
     * (PS-345), a reference that does not resolve (PS-348) or names another document
     * (PS-462, optional and not supported here), a pointer of another form (PS-461), a
     * cycle (PS-349). Mirrors expand_refs in tools/schema_interpreter.py.
     */
    @SuppressWarnings("unchecked")
    private static Map<String, Object> expandRawRefs(Map<String, Object> raw) {
        Object defsRaw = raw.get("definitions");
        if (defsRaw != null && !(defsRaw instanceof Map)) {
            throw new SchemaException("'definitions' must map names to field groups (PS-345)");
        }
        Map<String, Object> definitions = defsRaw == null ? Map.of() : (Map<String, Object>) defsRaw;
        for (Map.Entry<String, Object> e : definitions.entrySet()) {
            if (!(e.getValue() instanceof Map<?, ?> group) || !(group.get("fields") instanceof List)) {
                throw new SchemaException("definition '" + e.getKey()
                        + "' is not a field group with a `fields` array (PS-345)");
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : raw.entrySet()) {
            boolean carried = e.getKey().equals("definitions") || e.getKey().equals("test_vectors");
            out.put(e.getKey(), carried ? e.getValue() : expandNode(e.getValue(), definitions, new ArrayList<>()));
        }
        for (Map.Entry<String, Object> e : definitions.entrySet()) {
            List<String> stack = new ArrayList<>(List.of(e.getKey()));
            expandNode(((Map<String, Object>) e.getValue()).get("fields"), definitions, stack);
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    private static Object expandNode(Object node, Map<String, Object> definitions, List<String> stack) {
        if (node instanceof Map<?, ?> map) {
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : map.entrySet()) {
                out.put(String.valueOf(e.getKey()), expandNode(e.getValue(), definitions, stack));
            }
            return out;
        }
        if (node instanceof List<?> list) {
            List<Object> out = new ArrayList<>();
            for (Object item : list) {
                if (item instanceof Map<?, ?> m && m.containsKey("$ref")) {
                    out.addAll(resolveRef(m.get("$ref"), definitions, stack));
                } else {
                    out.add(expandNode(item, definitions, stack));
                }
            }
            return out;
        }
        return node;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> resolveRef(Object ref, Map<String, Object> definitions, List<String> stack) {
        if (!(ref instanceof String text) || !text.startsWith(REF_PREFIX)) {
            if (ref instanceof String text && text.contains("#") && !text.startsWith("#")) {
                throw new SchemaException("$ref " + text + " names another document; this implementation "
                        + "resolves only #/definitions/<name> (PS-462)");
            }
            throw new SchemaException("$ref " + ref + " is not of the form #/definitions/<name> (PS-461)");
        }
        String name = text.substring(REF_PREFIX.length());
        if (stack.contains(name)) {
            throw new SchemaException("$ref cycle: " + String.join(" -> ", stack) + " -> " + name + " (PS-349)");
        }
        Object group = definitions.get(name);
        if (!(group instanceof Map<?, ?> g) || !(g.get("fields") instanceof List<?> fields)) {
            throw new SchemaException("$ref " + text + " does not resolve to a field group (PS-348)");
        }
        List<String> deeper = new ArrayList<>(stack);
        deeper.add(name);
        return (List<Object>) expandNode(fields, definitions, deeper);
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

    /**
     * The bits a byte_group member covers, counted from the group's first bit (most
     * significant bit of its first byte), or null for a member that is neither a bit range
     * nor a bool. Mapping onto the group lets members of different widths be compared.
     */
    private static Set<Integer> byteGroupMemberBits(Map<String, Object> member) {
        Object type = member.get("type");
        int width, start, end;
        Matcher m = BIT_RANGE.matcher(type == null ? "" : type.toString());
        if (m.matches()) {
            width = Integer.parseInt(m.group(2));
            start = Integer.parseInt(m.group(3));
            end = Integer.parseInt(m.group(4));
        } else if ("bool".equals(type)) {
            width = 8;
            start = toInt(member.get("bit"), 0);
            end = start;
        } else {
            return null;
        }
        Set<Integer> bits = new HashSet<>();
        for (int b = start; b <= end; b++) bits.add(width - 1 - b);
        return bits;
    }

    /** PS-397: bit ranges within one byte_group must not overlap. They were accepted. */
    @SuppressWarnings("unchecked")
    private static void checkByteGroupOverlap(Object group) {
        List<?> members = group instanceof List<?> list ? list
                : group instanceof Map<?, ?> map && map.get("fields") instanceof List<?> fl ? fl
                : List.of();
        List<Map.Entry<Object, Set<Integer>>> seen = new ArrayList<>();
        for (Object raw : members) {
            if (!(raw instanceof Map)) continue;
            Map<String, Object> member = (Map<String, Object>) raw;
            Set<Integer> bits = byteGroupMemberBits(member);
            if (bits == null) continue;
            for (Map.Entry<Object, Set<Integer>> other : seen) {
                if (!Collections.disjoint(other.getValue(), bits)) {
                    throw new SchemaException("byte_group members '" + other.getKey() + "' and '"
                            + member.get("name") + "' overlap (PS-397)");
                }
            }
            seen.add(Map.entry(String.valueOf(member.get("name")), bits));
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
            if (t == FieldType.NUMBER || (t == FieldType.STRING && f.getValue() != null)) continue;
            if (t == FieldType.BITS || t == FieldType.BOOL) { total += f.getConsume(); continue; }
            if (t == FieldType.BYTES || t == FieldType.ASCII || t == FieldType.HEX
                    || t == FieldType.BASE64 || t == FieldType.SKIP) {
                if (f.getLength() <= 0) return 0;
                total += f.getLength();
                continue;
            }
            if (t.isInteger() || t.isFloat() || t == FieldType.UDEC || t == FieldType.SDEC) {
                total += t == FieldType.UDEC || t == FieldType.SDEC ? 1
                        : t.isFloat() ? (t == FieldType.F16 ? 2 : t == FieldType.F32 ? 4 : 8)
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
     * PS-430: a bitfield_string part is decimal, hex or hex:upper. PS-364: a byte_group
     * member declares no endian.
     */
    @SuppressWarnings("unchecked")
    private static void checkWave4(Map<String, Object> fm) {
        String at = "Field '" + fm.get("name") + "': ";
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
                if (raw instanceof List<?> part && part.size() > 2
                        && !List.of("decimal", "hex", "hex:upper").contains(part.get(2))) {
                    throw new SchemaException(at + "bitfield_string part format " + part.get(2)
                            + " is not one of decimal, hex, hex:upper (PS-430)");
                }
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

    /** PS-358: a literal's value matches its type, and it carries no arithmetic. */
    private static void checkLiteral(Map<String, Object> fm) {
        Object type = fm.get("type");
        if (!fm.containsKey("value") || !("string".equals(type) || "number".equals(type))) return;
        Object value = fm.get("value");
        String at = "Field '" + fm.get("name") + "': ";
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
            "sqrt", "abs", "pow", "log10", "log", "floor", "ceiling", "clamp", "op");
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
            if (ties != null && !"even".equals(ties) && !"away".equals(ties)) {
                throw new SchemaException(at + "round `ties` must be even or away (PS-390)");
            }
        }
    }

    /** The keys that make a field a construct, so it declares no type of its own. */
    private static final List<String> CONSTRUCT_KEYS =
            List.of("$ref", "flagged", "tlv", "byte_group", "match");

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
        f.setOn((String) fm.get("on"));
        f.setValue(fm.get("value"));
        f.setFormula((String) fm.get("formula"));

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
                    if (tm.get("clamp") instanceof List<?> bounds && bounds.size() == 2) {
                        t.setClamp(new double[] {toDouble(bounds.get(0)), toDouble(bounds.get(1))});
                    }
                    if (tm.containsKey("ties")) t.setTies(String.valueOf(tm.get("ties")));
                    transforms.add(t);
                }
            }
            f.setTransform(transforms);
        }
        
        // Lookup table
        Object lookupRaw = fm.get("lookup");
        if (lookupRaw instanceof Map) {
            Map<Integer, String> lookup = new HashMap<>();
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) lookupRaw).entrySet()) {
                if ("default".equals(String.valueOf(entry.getKey()))) {
                    f.setLookupDefault(String.valueOf(entry.getValue()));
                    continue;
                }
                int key = toInt(entry.getKey(), 0);
                lookup.put(key, String.valueOf(entry.getValue()));
            }
            f.setLookup(lookup);
        } else if (lookupRaw instanceof List) {
            // Sequence form, indexed from zero (PS-104). This was unparsed, so a
            // schema using it decoded a raw integer instead of its label.
            List<?> items = (List<?>) lookupRaw;
            Map<Integer, String> lookup = new HashMap<>();
            for (int i = 0; i < items.size(); i++) {
                lookup.put(i, String.valueOf(items.get(i)));
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
        }
        
        // Cases (for match/switch)
        Object casesRaw = fm.get("cases");
        if (casesRaw instanceof List) {
            List<Field.Case> cases = new ArrayList<>();
            for (Object cr : (List<?>) casesRaw) {
                if (cr instanceof Map) {
                    Map<String, Object> cm = (Map<String, Object>) cr;
                    Field.Case c = new Field.Case();
                    c.setCaseValue(cm.get("case") != null ? cm.get("case") : cm.get("match"));
                    c.setDefault(Boolean.TRUE.equals(cm.get("default")));
                    Object caseFieldsRaw = cm.get("fields");
                    if (caseFieldsRaw instanceof List) {
                        c.setFields(parseFields((List<Map<String, Object>>) caseFieldsRaw));
                    }
                    cases.add(c);
                }
            }
            f.setCases(cases);
        } else if (casesRaw instanceof Map && (f.getType() == FieldType.MATCH
                || f.getType() == FieldType.SWITCH)) {
            // Map-shaped cases on a declared match field, the same form the inline
            // block uses. Only the list form was read here.
            f.setCases(parseCaseMap(casesRaw));
        }

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
            // Insertion-ordered: encoding ranks the candidate cases for a channel and
            // needs that ranking to be reproducible, which a HashMap's iteration order is
            // not.
            Map<String, List<Field>> tlvCases = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) casesRaw).entrySet()) {
                String key = String.valueOf(entry.getKey());
                if (entry.getValue() instanceof List) {
                    tlvCases.put(key, parseFields((List<Map<String, Object>>) entry.getValue()));
                }
            }
            f.setTlvCases(tlvCases);
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

        // byte_group: fields packed into shared bytes. Written either as a list of
        // fields with a sibling `size`, or as {size: N, fields: [...]}.
        Object byteGroupRaw = fm.get("byte_group");
        if (byteGroupRaw instanceof Map<?, ?> groupMap) {
            if (groupMap.get("fields") instanceof List<?> groupFields) {
                f.setByteGroup(parseFields((List<Map<String, Object>>) groupFields));
            }
            f.setByteGroupSize(toInt(groupMap.get("size"), 1));
            if (groupMap.get("endian") instanceof String groupEndian) f.setGroupEndian(groupEndian);
        } else if (byteGroupRaw instanceof List<?> groupFields) {
            f.setByteGroup(parseFields((List<Map<String, Object>>) groupFields));
            f.setByteGroupSize(toInt(fm.get("size"), 1));
        }

        // Flagged construct
        Object flaggedRaw = fm.get("flagged");
        if (flaggedRaw instanceof Map) {
            Map<String, Object> flaggedMap = (Map<String, Object>) flaggedRaw;
            Field.FlaggedDef fd = new Field.FlaggedDef();
            fd.setField((String) flaggedMap.get("field"));
            
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

        // TLV inline
        Object tlvRaw = fm.get("tlv");
        if (tlvRaw instanceof Map) {
            Field tlvField = parseField((Map<String, Object>) tlvRaw);
            tlvField.setType(FieldType.TLV);
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
        InputContext context = in != null ? in : new InputContext(null, null, null);
        Integer fPort = context.fPort();
        DecodeContext ctx = new DecodeContext(data, endian);
        ctx.setRepeatOnlyNames(repeatOnlyNames);
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
        Map<String, Object> entries = new LinkedHashMap<>();
        for (String key : result.keySet()) {
            if (key.startsWith("_")) continue;              // `_quality`, `_warnings`
            Map<?, ?> source = ctx.getProducers().get(key);
            if (source == null) source = declared.get(key);
            entries.put(key, source != null ? Meta.fieldMeta(source, null) : new LinkedHashMap<>());
        }
        meta.put("fields", entries);
        result.put("_meta", meta);
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
        return new Encoder(this, fields).run(data);
    }

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
        checkDirection(fPort, direction);
        return new Encoder(this, resolveFields(fPort)).run(data);
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
                Object matchResult = decodeMatch(field.getMatchInline(), ctx);
                if (matchResult instanceof Map<?, ?> matchMap) {
                    for (Map.Entry<?, ?> entry : matchMap.entrySet()) {
                        String key = String.valueOf(entry.getKey());
                        result.put(key, entry.getValue());
                        ctx.setVariable(key, entry.getValue());
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
                    ctx.setVariable(entry.getKey(), entry.getValue());
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
                // A mapping lookup with no entry and no default (PS-269).
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
                // Variables are keyed by the schema-level name so $references keep
                // working when name_from is in play (PS-267).
                ctx.setVariable(field.getName(), value);
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
        Object flagsVal = ctx.getVariable(fd.getField());
        if (flagsVal == null) {
            throw new SchemaException.DecodeException("Flagged field reference not found: " + fd.getField());
        }
        int flags = toInt(flagsVal, 0);
        
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
                byte[] data = ctx.read(length);
                long raw = ctx.decodeUnsigned(data, fieldEndian);
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
                byte[] data = ctx.read(length);
                String str = new String(data, StandardCharsets.US_ASCII);
                value = str.replace("\0", "").trim();
            }
            
            case HEX -> {
                byte[] data = ctx.read(length);
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
                byte[] data = ctx.read(length);
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
                Map<Integer, String> values = field.getValues();
                if (values != null && values.containsKey(raw)) {
                    value = values.get(raw);
                } else if (field.getEnumDefault() != null) {
                    // An unmapped value reports the declared default (PS-068).
                    value = field.getEnumDefault();
                } else {
                    value = (long) raw;
                }
            }
            
            case OBJECT -> {
                value = decodeFields(field.getFields(), ctx);
            }
            
            case MATCH, SWITCH -> {
                value = decodeMatch(field, ctx);
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
     * What happens to a value once it is read: formula or modifiers, lookup, and the
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
        // Apply formula if present (takes precedence). A computed field has already
        // had its own arithmetic applied by decodeComputed, in the order the
        // interpreter uses: polynomial, then modifiers, then transform. Running the
        // block below over it again would apply the modifiers twice, and would apply
        // them to a guard's fallback, which must be reported as declared.
        if (field.getFormula() != null && !field.getFormula().isEmpty() && field.getType() != FieldType.NUMBER) {
            if (value instanceof Number) {
                value = FormulaEvaluator.evaluate(field.getFormula(), ((Number) value).doubleValue(), ctx);
            }
        } else if (reportsAsInteger(field)) {
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
                return OMITTED;
            }
        }
        
        // PS-428: outside the range is no reading, not a flagged one, so the field is
        // absent - neither reported nor bound.
        if (fieldRules && !asDeclared && !lookedUp && Wave6b.rangeOmits(field, value)) {
            return RANGE_ABSENT;
        }

        // Store variable
        if (field.getVar() != null && !field.getVar().isEmpty()) {
            ctx.setVariable(field.getVar(), value);
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
                && (field.getTransform() == null || field.getTransform().isEmpty())
                && (field.getFormula() == null || field.getFormula().isEmpty());
    }

    private Object decodeMatch(Field field, DecodeContext ctx) {
        int matchValue;
        // What this construct contributes on its own: the discriminator under `name`,
        // where it was read here and named. Null where there is nothing to report.
        Map<String, Object> inline = null;
        
        if (field.isMatchRemaining()) {
            // PS-414: the bytes from here to the end of the region, which a reserving
            // repeat or tlv has already narrowed. Nothing is read.
            matchValue = ctx.remaining();
        } else if (field.getOn() != null && !field.getOn().isEmpty()) {
            String varName = field.getOn().startsWith("$") ? field.getOn().substring(1) : field.getOn();
            Object val = ctx.getVariable(varName);
            if (val == null) {
                throw new SchemaException.DecodeException("Variable not found: $" + varName);
            }
            matchValue = toInt(val, 0);
        } else {
            int length = field.getLength() > 0 ? field.getLength() : 1;
            byte[] data = ctx.read(length);
            matchValue = (int) ctx.decodeUnsigned(data, ctx.getEndian());

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
                return mergeMatch(inline, decodeFields(c.getFields(), ctx));
            }

            Object caseVal = c.getCaseValue();
            if (caseVal == null) continue;

            if (matchesCase(matchValue, caseVal)) {
                return mergeMatch(inline, decodeFields(c.getFields(), ctx));
            }
        }

        // `default` decides what an unmatched value means and defaults to "error".
        // Nothing read the key, so every value of it behaved as "skip" and a schema
        // declaring a fallback got none.
        Object fallback = field.getMatchDefault();
        if (fallback instanceof List<?> fallbackFields) {
            // Already Fields: the parser converts the `default:` list once (CR-2026-027).
            return mergeMatch(inline, decodeFields((List<Field>) fallbackFields, ctx));
        }
        if (fallback == null || !"skip".equals(String.valueOf(fallback))) {
            throw new SchemaException.DecodeException(
                    "No matching case for value " + matchValue);
        }
        return inline;
    }

    /**
     * Whether a discriminator satisfies one case key, matching what the Python
     * interpreter's {@code _match_case_pattern} accepts.
     *
     * <p>The string range spelling {@code "2..5"} was missing: only numbers, lists and
     * {@code {min,max}} maps were compared, so a range key matched nothing at all and the
     * construct decoded silently empty (CR-2026-020).
     */
    private int rangeBound(String text) {
        return Integer.parseInt(text.trim());
    }

    private boolean matchesCase(int matchValue, Object caseVal) {
        if (caseVal instanceof Number number) {
            return matchValue == number.intValue();
        }
        if (caseVal instanceof String text && parseListCaseKey(text) != null) {
            // A quoted flow sequence, "[1, 2, 3]", matches any element (PS-398).
            return parseListCaseKey(text).contains((long) matchValue);
        }
        if (caseVal instanceof String text) {
            int separator = text.indexOf("..");
            if (separator > 0) {
                try {
                    return matchValue >= rangeBound(text.substring(0, separator))
                            && matchValue <= rangeBound(text.substring(separator + 2));
                } catch (NumberFormatException ignored) {
                    return false;
                }
            }
            try {
                return matchValue == rangeBound(text);
            } catch (NumberFormatException ignored) {
                return false;
            }
        }
        if (caseVal instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Number number && matchValue == number.intValue()) {
                    return true;
                }
            }
            return false;
        }
        if (caseVal instanceof Map<?, ?> rangeMap) {
            int minVal = toInt(rangeMap.get("min"), Integer.MIN_VALUE);
            int maxVal = toInt(rangeMap.get("max"), Integer.MAX_VALUE);
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
        
        // A composite tag's width is its tag_fields' summed widths.
        int tagWidth = tagSize;
        if (field.getTagFields() != null && !field.getTagFields().isEmpty()) {
            tagWidth = 0;
            for (Field tf : field.getTagFields()) {
                tagWidth += tagFieldWidth(tf);
            }
        }

        while (ctx.remaining() > 0) {
            int entryStart = ctx.getOffset();
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
                    byte[] data = ctx.read(tagFieldWidth(tf));
                    int val = (int) ctx.decodeUnsigned(data, ctx.getEndian());
                    if (tf.getName() != null) {
                        tagValues.put(tf.getName(), val);
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
                Map<String, Object> caseResult = decodeFields(caseFields, ctx);
                
                if (merge) {
                    for (Map.Entry<String, Object> entry : caseResult.entrySet()) {
                        String k = entry.getKey();
                        Object v = entry.getValue();
                        if (result.containsKey(k)) {
                            Object existing = result.get(k);
                            if (existing instanceof List) {
                                ((List<Object>) existing).add(v);
                            } else {
                                List<Object> arr = new ArrayList<>();
                                arr.add(existing);
                                arr.add(v);
                                result.put(k, arr);
                            }
                        } else {
                            result.put(k, v);
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
            Object value = ctx.getVariable(reference);
            String replacement;
            if (value == null) {
                missing.add(reference);
                replacement = "";
            } else if (value instanceof Number
                    && ((Number) value).doubleValue() == ((Number) value).longValue()) {
                replacement = String.valueOf(((Number) value).longValue());
            } else {
                replacement = String.valueOf(value);
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
    private List<Map<String, Object>> decodeRepeat(Field field, DecodeContext ctx) {
        int maxIterations = field.getMax() > 0 ? field.getMax() : 1000;
        int minIterations = field.getMin();

        List<Map<String, Object>> result = new ArrayList<>();
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
                    } catch (SchemaException.DecodeException e) {
                        if (e.getMessage() == null || !e.getMessage().contains("Buffer underflow")) throw e;
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
                               Map<String, Object> carryState, List<Map<String, Object>> result) {
        Map<String, Object> outer = ctx.snapshotVariables();
        Set<String> savedNames = ctx.getElementNames();
        List<Field> members = repeat.getFields() == null ? List.of() : repeat.getFields();
        Set<String> names = new HashSet<>();
        for (Field member : members) names.add(member.getName());
        ctx.setElementNames(names);
        if (repeat.getIndex() != null) {
            ctx.setVariable(repeat.getIndex(), (long) iterations[0]);      // PS-366
        }
        ctx.getVariables().putAll(carryState);                              // PS-379
        Map<String, Object> element;
        boolean keep;
        try {
            element = decodeFields(members, ctx);
            for (String carried : carryState.keySet()) {
                if (ctx.getVariables().containsKey(carried)) {
                    carryState.put(carried, ctx.getVariable(carried));
                }
            }
            keep = repeat.getPresentIf() == null || guardPasses(repeat.getPresentIf(), ctx);
        } finally {
            // PS-368: an element's names do not outlive it.
            ctx.restoreVariables(outer);
            ctx.setElementNames(savedNames);
        }
        iterations[0]++;
        if (keep) result.add(element);                                      // PS-386
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
                String format = part.size() >= 3 ? String.valueOf(part.get(2)) : "decimal";
                
                long mask = (1L << bitLen) - 1;
                long raw = (intVal >> bitOff) & mask;
                
                // PS-430: hex lower case, hex:upper upper case; any other format is
                // refused when the schema is loaded.
                if ("hex".equals(format)) {
                    partStrs.add(Long.toHexString(raw));
                } else if ("hex:upper".equals(format)) {
                    partStrs.add(Long.toHexString(raw).toUpperCase());
                } else {
                    partStrs.add(String.valueOf(raw));
                }
            }
        }
        
        return prefix + String.join(delimiter, partStrs);
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
        long groupValue = ctx.decodeUnsigned(ctx.peek(group.getByteGroupSize(), 0), endian);

        for (Field member : group.getByteGroup()) {
            ctx.setOffset(start);
            String name = member.getName();
            if (name == null || name.isEmpty()) continue;
            ctx.produced(member, null);                             // PS-490
            try {
                Object value;
                if (member.getType() == FieldType.BITS) {
                    value = applyGroupMember(extractRange(groupValue, member.isSignedBits(),
                            member.getBitOffset(), member.getBits()), member, ctx);
                } else if (member.getType() == FieldType.BOOL) {
                    int bit = Math.max(0, member.getBoolBit());
                    value = ((groupValue >>> bit) & 1) == 1;
                } else {
                    value = decodeField(member, ctx, false);
                }
                if (value == OMITTED || value == null) continue;
                ctx.setVariable(name, value);
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
    private static final List<String> GUARD_OPS = List.of("gt", "gte", "lt", "lte", "eq", "ne");

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
                    condition.setOperand(toDouble(cm.get(op)));
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
                Object v = normalizeOutput(item);
                if (v != null) {
                    out.add(v);
                }
            }
            return out;
        }
        return value;
    }

    private Object decodeComputed(Field field, DecodeContext ctx) {
        if (field.getFormula() != null && !field.getFormula().isEmpty()) {
            return FormulaEvaluator.evaluate(field.getFormula(), 0, ctx);
        }

        boolean computed = field.getRef() != null || field.getCompute() != null;
        if (computed && field.getGuard() != null && !guardPasses(field.getGuard(), ctx)) {
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
    private static double applyArithmetic(double value, Field field) {
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
                java.math.RoundingMode mode = "away".equals(stage.getTies())
                        ? java.math.RoundingMode.HALF_UP : java.math.RoundingMode.HALF_EVEN;
                value = new java.math.BigDecimal(value).setScale(decimals, mode).doubleValue();
                continue;
            }
            if (stage.getFloor() != null) { value = Math.max(value, stage.getFloor()); continue; }
            if (stage.getCeiling() != null) { value = Math.min(value, stage.getCeiling()); continue; }
            if (stage.getClamp() != null) {
                value = Math.max(stage.getClamp()[0], Math.min(stage.getClamp()[1], value));
                continue;
            }
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
            default -> throw new SchemaException.DecodeException(
                    "Unknown compute op: " + compute.getOp());
        };
    }

    /** Resolve a {@code $field} reference against decoded variables, or a literal. */
    private double resolveOperand(Object spec, DecodeContext ctx) {
        if (spec instanceof String text && text.startsWith("$")) {
            Object value = ctx.ref(text.substring(1));     // PS-368
            return value instanceof Number number ? number.doubleValue() : 0.0;
        }
        Double literal = toDouble(spec);
        return literal == null ? 0.0 : literal;
    }

    private boolean guardPasses(Field.Guard guard, DecodeContext ctx) {
        for (Field.Condition condition : guard.getWhen()) {
            if (!condition.getField().startsWith("$")) continue;
            Object raw = ctx.ref(condition.getField().substring(1));     // PS-368
            double value = raw instanceof Number number ? number.doubleValue() : 0.0;
            double operand = condition.getOperand() == null ? 0.0 : condition.getOperand();
            boolean passed = switch (condition.getOp()) {
                case "gt" -> value > operand;
                case "gte" -> value >= operand;
                case "lt" -> value < operand;
                case "lte" -> value <= operand;
                case "eq" -> value == operand;
                case "ne" -> value != operand;
                default -> true;
            };
            if (!passed) return false;
        }
        return true;
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
