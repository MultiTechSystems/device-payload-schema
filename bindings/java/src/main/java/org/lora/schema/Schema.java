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
    private static final Pattern BIT_RANGE = Pattern.compile("u(\\d+)\\[(\\d+):(\\d+)\\]");

    private String name;
    private int version;
    private String description;
    private String endian = "big";
    /** Applies to the whole schema where it has no ports (PS-291). */
    private String direction;
    private List<Field> fields;
    private Map<String, PortDef> ports;

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
        Schema schema = new Schema();
        
        schema.name = (String) raw.getOrDefault("name", "unnamed");
        schema.version = toInt(raw.get("version"), 1);
        schema.description = (String) raw.get("description");
        schema.endian = (String) raw.getOrDefault("endian", "big");
        schema.direction = (String) raw.get("direction");
        
        // Parse fields, splicing any `$ref` into the list first.
        Object fieldsRaw = raw.get("fields");
        if (fieldsRaw instanceof List) {
            schema.fields = parseFields(
                    expandRefs((List<Map<String, Object>>) fieldsRaw, raw, 0));
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
            // A port's field list may carry a `$ref` too - `definitions` are
            // schema-level, so they resolve against the document root.
            pd.setFields(parseFields(
                    expandRefs((List<Map<String, Object>>) fieldsRaw, root, 0)));
        }

        return pd;
    }

    @SuppressWarnings("unchecked")
    private static List<Field> parseFields(List<Map<String, Object>> fieldsRaw) {
        List<Field> fields = new ArrayList<>();
        if (fieldsRaw == null) return fields;

        for (Map<String, Object> fm : fieldsRaw) {
            fields.add(parseField(fm));
        }
        return fields;
    }

    /**
     * Splice `$ref: '#/definitions/name'` entries into the field list they appear in.
     *
     * <p>Resolved at parse time, and the referenced definition's {@code fields:} are
     * spliced rather than nested: a nested container with no {@code type: object} is
     * never descended into, so every field inside it would report as missing.
     *
     * <p>Only local {@code #/definitions/...} references resolve here, matching the
     * other implementations. Cross-file references are a pre-step
     * (tools/schema_preprocessor.py) so the interpreters need no loader.
     *
     * <p>This binding had no {@code $ref} support at all until the `header:` block was
     * removed and a conformance fixture pointed the replacement at it.
     */
    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> expandRefs(
            List<Map<String, Object>> fieldsRaw, Map<String, Object> root, int depth) {
        if (fieldsRaw == null) return null;
        List<Map<String, Object>> out = new ArrayList<>();
        // Guard against a definition that refers to itself, directly or in a cycle.
        if (depth > 16) return fieldsRaw;

        for (Map<String, Object> fm : fieldsRaw) {
            Object refRaw = fm.get("$ref");
            if (!(refRaw instanceof String ref)) {
                out.add(fm);
                continue;
            }
            List<Map<String, Object>> target = definitionFields(ref, root);
            if (target == null) {
                // Unresolvable: keep the entry so the field simply produces nothing,
                // rather than dropping it and shifting every later offset.
                out.add(fm);
                continue;
            }
            out.addAll(expandRefs(target, root, depth + 1));
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> definitionFields(String ref, Map<String, Object> root) {
        String prefix = "#/definitions/";
        if (!ref.startsWith(prefix)) return null;
        Object defsRaw = root.get("definitions");
        if (!(defsRaw instanceof Map)) return null;
        Object def = ((Map<String, Object>) defsRaw).get(ref.substring(prefix.length()));
        if (!(def instanceof Map)) return null;
        Object fields = ((Map<String, Object>) def).get("fields");
        return fields instanceof List ? (List<Map<String, Object>>) fields : null;
    }

    @SuppressWarnings("unchecked")
    private static Field parseField(Map<String, Object> fm) {
        Field f = new Field();
        
        f.setName((String) fm.get("name"));
        String rawType = (String) fm.get("type");
        f.setType(FieldType.fromString(rawType));
        // `length: remaining` (PS-014) is carried as a negative sentinel; toInt would
        // otherwise silently return the 0 default and the field would read one byte.
        Object lengthSpec = fm.get("length");
        if (lengthSpec instanceof String s && s.trim().equalsIgnoreCase("remaining")) {
            f.setLength(-1);
        } else {
            f.setLength(toInt(lengthSpec, 0));
        }
        f.setByteOffset(toInt(fm.get("byte_offset"), 0));
        f.setBitOffset(toInt(fm.get("bit_offset"), 0));
        f.setBits(toInt(fm.get("bits"), 0));
        f.setConsume(toInt(fm.get("consume"), 0));
        f.setEndian((String) fm.get("endian"));

        // A `u8[lo:hi]` range is a bit field. FieldType.fromString does not recognise
        // the bracket form and fell through to U8, so the whole byte was read instead
        // of the bits: a packed flag byte reported its raw value, which is why
        // em310-tilt's threshold_x decoded as 17 rather than "trigger".
        Matcher bitRange = BIT_RANGE.matcher(rawType == null ? "" : rawType.trim());
        if (bitRange.matches()) {
            int start = Integer.parseInt(bitRange.group(2));
            int end = Integer.parseInt(bitRange.group(3));
            f.setBitOffset(start);
            f.setBits(end - start + 1);
            // The base width is part of the type: u24[4:23] takes bits 4-23 of a
            // 24-bit big-endian value, so all three bytes are read before masking.
            f.setBitBaseBytes(Math.max(1, Integer.parseInt(bitRange.group(1)) / 8));
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
        
        // byte_group: fields packed into shared bytes. Written either as a list of
        // fields with a sibling `size`, or as {size: N, fields: [...]}.
        Object byteGroupRaw = fm.get("byte_group");
        if (byteGroupRaw instanceof Map<?, ?> groupMap) {
            if (groupMap.get("fields") instanceof List<?> groupFields) {
                f.setByteGroup(parseFields((List<Map<String, Object>>) groupFields));
            }
            f.setByteGroupSize(toInt(groupMap.get("size"), 1));
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
            if (matchMap.get("var") instanceof String matchVar) {
                matchField.setVar(matchVar);
            }
            if (matchMap.containsKey("default")) {
                matchField.setMatchDefault(matchMap.get("default"));
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

    // Decode methods
    public Map<String, Object> decode(byte[] data) {
        DecodeContext ctx = new DecodeContext(data, endian);
        Map<String, Object> result = new LinkedHashMap<>();

        // Decode main fields
        Map<String, Object> fieldsResult = decodeFields(fields, ctx);
        result.putAll(fieldsResult);
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
        Map<String, Object> result = new LinkedHashMap<>();

        // Decode resolved fields
        Map<String, Object> fieldsResult = decodeFields(resolvedFields, ctx);
        result.putAll(fieldsResult);
        reportWarnings(result, ctx);

        return result;
    }

    /**
     * Copies anything the decode wanted to say into the result under {@code _warnings}.
     *
     * <p>Absent unless something was collected, so a clean decode carries no extra key.
     */
    private static void reportWarnings(Map<String, Object> result, DecodeContext ctx) {
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
            
            Object value = decodeField(field, ctx);

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
                        result.put(resolveFieldName(field, ctx), reported);
                    }
                }
                // Variables are keyed by the schema-level name so $references keep
                // working when name_from is in play (PS-267).
                ctx.setVariable(field.getName(), value);
            }
        }
        
        return result;
    }

    private Map<String, Object> decodeFlagged(Field.FlaggedDef fd, DecodeContext ctx) {
        Object flagsVal = ctx.getVariable(fd.getField());
        if (flagsVal == null) {
            throw new SchemaException.DecodeException("Flagged field reference not found: " + fd.getField());
        }
        int flags = toInt(flagsVal, 0);
        
        Map<String, Object> result = new LinkedHashMap<>();
        
        for (Field.FlaggedGroup group : fd.getGroups()) {
            int isPresent = (flags >> group.getBit()) & 1;
            if (isPresent != 0) {
                Map<String, Object> groupResult = decodeFields(group.getFields(), ctx);
                result.putAll(groupResult);
            }
        }
        
        return result;
    }

    private Object decodeField(Field field, DecodeContext ctx) {
        int length = field.getEffectiveLength();
        String fieldEndian = field.getEffectiveEndian(ctx.getEndian());
        
        Object value = null;
        
        switch (field.getType()) {
            // The type fixes both orders, so fieldEndian is deliberately not consulted
            // (PS-272): honouring it would make u32le16 with endian little a second
            // spelling of little-endian u32.
            case U32LE16, S32LE16 -> {
                byte[] data = ctx.read(4);
                long low = ((data[0] & 0xFFL) << 8) | (data[1] & 0xFFL);
                long high = ((data[2] & 0xFFL) << 8) | (data[3] & 0xFFL);
                long combined = low | (high << 16);
                value = field.getType() == FieldType.S32LE16 && combined >= 0x80000000L
                        ? combined - 0x100000000L
                        : combined;
            }

            case U8, U16, U24, U32, U64, BYTE, UINT -> {
                byte[] data = ctx.read(length);
                long raw = ctx.decodeUnsigned(data, fieldEndian);
                if (length >= 8 && raw < 0) {
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
            
            case BOOL -> {
                byte[] data = ctx.peek(1, field.getByteOffset());
                value = ctx.decodeBits(data[0] & 0xFF, field.getBitOffset(), 1) != 0;
            }
            
            case BITS -> {
                // Read the whole base width, not just the first byte: a range wider
                // than a byte (u24[0:11] for a packed 12-bit humidity) decoded from
                // byte zero alone and reported a value with no error.
                int baseBytes = Math.max(1, field.getBitBaseBytes());
                byte[] data = ctx.peek(baseBytes, field.getByteOffset());
                long base = 0;
                for (byte b : data) {
                    base = (base << 8) | (b & 0xFF);
                }
                int numBits = field.getBits() > 0 ? field.getBits() : 1;
                long mask = numBits >= 64 ? -1L : (1L << numBits) - 1;
                value = (base >>> field.getBitOffset()) & mask;
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
                value = field.getValue();
            }
            
            case NUMBER -> {
                value = decodeComputed(field, ctx);
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
        
        // Apply lookup. A mapping's keys need not start at zero or be contiguous
        // (PS-268); an unmatched value omits the field rather than reporting the raw
        // integer under a name that promises a label, unless a default is declared
        // (PS-269). A sequence is indexed from zero (PS-104) and an out-of-bounds
        // index is an error (PS-105), not the raw value: the payload does not match
        // the schema's shape at all.
        if (field.getLookup() != null && value instanceof Number) {
            int intVal = ((Number) value).intValue();
            if (field.getLookup().containsKey(intVal)) {
                value = field.getLookup().get(intVal);
            } else if (field.getLookupDefault() != null) {
                value = field.getLookupDefault();
            } else if (field.isLookupSequence()) {
                throw new SchemaException.DecodeException(String.format(
                    "lookup index %d out of bounds for %d entries",
                    intVal, field.getLookup().size()));
            } else {
                return OMITTED;
            }
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
        
        if (field.getOn() != null && !field.getOn().isEmpty()) {
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
            return mergeMatch(inline,
                    decodeFields(parseFields((List<Map<String, Object>>) fallbackFields), ctx));
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

    @SuppressWarnings("unchecked")
    private Map<String, Object> decodeTLV(Field field, DecodeContext ctx) {
        int tagSize = field.getTagSize() > 0 ? field.getTagSize() : 1;
        int lengthSize = field.getLengthSize();
        boolean merge = field.getMerge() == null || field.getMerge();
        String unknownMode = field.getUnknown() != null ? field.getUnknown() : "skip";
        
        Map<String, Object> result = new LinkedHashMap<>();
        List<Map<String, Object>> channels = new ArrayList<>();
        
        while (ctx.remaining() > 0) {
            int entryStart = ctx.getOffset();
            List<Integer> tag = new ArrayList<>();
            Map<String, Integer> tagValues = new HashMap<>();
            
            if (field.getTagFields() != null && !field.getTagFields().isEmpty()) {
                for (Field tf : field.getTagFields()) {
                    int tfLength = tf.getLength() > 0 ? tf.getLength() : 1;
                    byte[] data = ctx.read(tfLength);
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
                byte[] data = ctx.read(lengthSize);
                dataLength = (int) ctx.decodeUnsigned(data, ctx.getEndian());
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
                        label, entryStart, ctx.getData().length - entryStart,
                        ctx.getData().length));
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

    private List<Map<String, Object>> decodeRepeat(Field field, DecodeContext ctx) {
        int maxIterations = field.getMax() > 0 ? field.getMax() : 1000;
        int minIterations = field.getMin();
        
        List<Map<String, Object>> result = new ArrayList<>();
        
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
            
            count = Math.min(count, maxIterations);
            
            for (int i = 0; i < count; i++) {
                result.add(decodeFields(field.getFields(), ctx));
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
            int iterations = 0;
            
            while (ctx.getOffset() < endOffset && iterations < maxIterations) {
                result.add(decodeFields(field.getFields(), ctx));
                iterations++;
            }
            
            if (ctx.getOffset() != endOffset) {
                throw new SchemaException.DecodeException(
                    String.format("Repeat byte_length mismatch: expected end at %d, got %d", endOffset, ctx.getOffset()));
            }
        } else if ("end".equals(field.getUntil())) {
            int iterations = 0;
            while (ctx.remaining() > 0 && iterations < maxIterations) {
                result.add(decodeFields(field.getFields(), ctx));
                iterations++;
            }
        } else {
            throw new SchemaException.DecodeException("Repeat field must specify one of: count, byte_length, or until");
        }
        
        if (result.size() < minIterations) {
            throw new SchemaException.DecodeException(
                String.format("Repeat produced %d elements, but minimum is %d", result.size(), minIterations));
        }
        
        return result;
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
                
                if ("hex".equals(format)) {
                    // Lowercase (PS-074), matching the vendor codecs and the
                    // generated JS.
                    partStrs.add(Long.toHexString(raw));
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

        for (Field member : group.getByteGroup()) {
            ctx.setOffset(start);
            String name = member.getName();
            if (name == null || name.isEmpty()) continue;
            try {
                Object value = decodeField(member, ctx);
                if (value == OMITTED || value == null) continue;
                ctx.setVariable(name, value);
                if (!name.startsWith("_")) {
                    result.put(resolveFieldName(member, ctx), value);
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
            // A failing guard reports the declared fallback untouched - no modifiers,
            // no transform. Checking the guard first is also what keeps a guarded
            // division by zero from ever running.
            return field.getGuard().hasElse() ? field.getGuard().getElseValue() : Double.NaN;
        }

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
            // transform stages, which would otherwise operate on the sentinel.
            if (isComputeOmitted(value)) {
                return null;
            }
            return applyTransform(value, field.getTransform());
        }

        return field.getValue();
    }

    /**
     * Apply a field's bare modifiers and then its transform stages. Both run when both
     * are present - a field may scale with `mult` and then round with a stage - which
     * an either/or chain here used to get wrong, dropping the modifier. The modifiers
     * run in the canonical order mult, div, add, whatever order the keys were written
     * in (PS-101); the stages run in list order.
     */
    private static double applyArithmetic(double value, Field field) {
        if (field.getMult() != null) value *= field.getMult();
        if (field.getDiv() != null && field.getDiv() != 0) value /= field.getDiv();
        if (field.getAdd() != null) value += field.getAdd();
        return applyTransform(value, field.getTransform());
    }

    private static double applyTransform(double value, List<Field.Transform> stages) {
        if (stages == null) return value;
        for (Field.Transform stage : stages) {
            if ("round".equals(stage.getOp())) {
                int decimals = stage.getDecimals() == null ? 0 : stage.getDecimals();
                // Half-to-even, matching the interpreter's rounding. Half-up would
                // disagree with it on exact halves, which test vectors do contain.
                value = new java.math.BigDecimal(value)
                        .setScale(decimals, java.math.RoundingMode.HALF_EVEN)
                        .doubleValue();
                continue;
            }
            // Unary maths stages, each exclusive of the others and of the arithmetic
            // ops, in the order the Python interpreter checks them. The domain clamps
            // match it exactly: sqrt of a negative and log of a non-positive would
            // otherwise yield NaN and poison every later stage, where the interpreter
            // yields 0 and log(1e-10).
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
                value = Math.log10(Math.max(1e-10, value));
                continue;
            }
            if (Boolean.TRUE.equals(stage.getLog())) {
                value = Math.log(Math.max(1e-10, value));
                continue;
            }
            if (stage.getMult() != null) value *= stage.getMult();
            if (stage.getDiv() != null && stage.getDiv() != 0) value /= stage.getDiv();
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
            Object value = ctx.getVariable(text.substring(1));
            return value instanceof Number number ? number.doubleValue() : 0.0;
        }
        Double literal = toDouble(spec);
        return literal == null ? 0.0 : literal;
    }

    private boolean guardPasses(Field.Guard guard, DecodeContext ctx) {
        for (Field.Condition condition : guard.getWhen()) {
            if (!condition.getField().startsWith("$")) continue;
            Object raw = ctx.getVariable(condition.getField().substring(1));
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
