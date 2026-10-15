// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Encodes a data map back to payload bytes - the inverse of {@link Schema#decode}.
 *
 * <p>This binding had no encoder at all: {@code Varint.encode} and the exception type
 * existed, nothing else. It is ported from {@code tools/schema_interpreter.py}, whose
 * round-trip corpus is what found the gaps worth having: encoding has to undo a field's
 * lookup, then its transform chain, then its canonical modifiers, in that order, and it
 * has to rebuild the framing of every construct rather than emitting bare values.
 *
 * <p>Two properties of the constructs need saying, because they are not obvious from the
 * decode side:
 *
 * <ul>
 *   <li>A TLV payload is flattened by decoding, so the channels are recovered from which
 *       field names the data carries. Their order is the order those names appear in the
 *       map, which for output straight from {@code decode} is the order they were read -
 *       a {@link LinkedHashMap}, so unlike Go this needs no separate ordered API. Every
 *       name belongs to exactly one channel, and where two cases define the same name the
 *       arithmetic decides which of them could have produced the value.
 *   <li>A {@code match} on {@code field: $var} read no bytes of its own; the variable came
 *       from an earlier field that the main loop encodes. Writing the discriminator here
 *       would duplicate it, so only the inline form (with {@code length}) writes one.
 * </ul>
 *
 * <p>{@code encode(decode(payload)) == payload} cannot hold everywhere. A {@code skip}
 * field's bytes are not recoverable from output that omits them, a rounding stage discards
 * precision, and a {@code lookup} {@code default} label stands for every value the table
 * does not list (PS-269). Those cases are reported through {@link EncodeResult}, not
 * papered over with wrong bytes.
 */
final class Encoder {

    private final Schema schema;
    private final List<Field> fields;
    private final boolean little;
    private final List<String> warnings = new ArrayList<>();
    private final List<String> errors = new ArrayList<>();
    /** The decode record this encode follows (PS-573); null where none was given. */
    private DecodeRecord rec;
    /** Names whose value the record, not the input, supplied. */
    private final Set<String> recordSupplied = new java.util.HashSet<>();
    /** The names the encode's input gives. */
    private final Set<String> inputNames = new java.util.HashSet<>();

    Encoder(Schema schema, List<Field> fields) {
        this.schema = schema;
        this.fields = fields == null ? List.of() : fields;
        this.little = "little".equalsIgnoreCase(schema.getEndian());
    }

    // --- entry point ---------------------------------------------------------------

    EncodeResult run(Map<String, Object> data) {
        return run(data, null);
    }

    /**
     * Encodes {@code data}, following {@code decodeRecord} - or, where that is null, the
     * {@code _decode} the input carries - as PS-573 says: each recorded case, discriminator,
     * internal value and flags value is used where the input gives none, and input that
     * contradicts the record is rejected. Where neither the input nor the record gives a
     * match's case, this encoder derives it (PS-551, PS-563, PS-564), as PS-574 permits.
     * {@code _meta} describes a decode and is not input either.
     */
    @SuppressWarnings("unchecked")
    EncodeResult run(Map<String, Object> data, Map<?, ?> decodeRecord) {
        Map<String, Object> input = data == null ? Map.of() : data;
        if (input.containsKey("_decode") || input.containsKey("_meta")) {
            if (decodeRecord == null && input.get("_decode") instanceof Map<?, ?> given) {
                decodeRecord = given;
            }
            Map<String, Object> stripped = new LinkedHashMap<>(input);
            stripped.remove("_decode");
            stripped.remove("_meta");
            input = stripped;
        }
        if (decodeRecord != null) rec = new DecodeRecord(schema.pointers(), decodeRecord);
        inputNames.addAll(input.keySet());
        ByteBuf out = new ByteBuf();

        // PS-551: a value the input omits but the rest of it fixes - a repeat's count or
        // byte_length field, a match's discriminator, a flagged mask - is derived before
        // anything is written, and a supplied one that disagrees is rejected. The flagged
        // mask was the one derivation already made, and it overwrote a supplied mask.
        try {
            input = deriveInputs(fields, input);
        } catch (RuntimeException e) {
            errors.add(e.getMessage());
            return new EncodeResult(EMPTY, warnings, errors);
        }

        // deriveInputs also makes the masks the groups fix visible to the constructs before
        // the block, as the decoder's variables are: dnt binds its mask with an inline
        // match (CR-2026-136).

        // Bit ranges sharing a byte are packed together rather than a byte apiece
        // (CR-2026-024), so the whole list is walked at once: a run cannot be found by
        // looking at one field in isolation.
        byte[] frame = encodeWithBitfieldRuns(fields, input, true);
        if (errors.isEmpty()) {
            try {
                checkPeeks(frame);                              // PS-551 (CR-2026-141)
            } catch (RuntimeException e) {
                errors.add(e.getMessage());
            }
        }
        if (errors.isEmpty()) {
            // PS-564: the bytes from each `remaining` match to the end select its case.
            for (TailCheck c : tailChecks) {
                String selected = remainingSelection(c.match, c.tail);
                if (!java.util.Objects.equals(selected, c.chosen)) {
                    errors.add(remainingMismatchMessage(c.match, c.tail, selected, c.chosen));
                    break;
                }
            }
        }
        if (errors.isEmpty() && frame.length == 0) {
            // PS-442: the last line against a construct that wrote nothing and said so to
            // no one - a match with no case chosen, a tlv entry no case claimed.
            String empty = emptyPayloadError(fields, input);
            if (empty != null) errors.add(empty);
        }
        out.write(frame);

        // A rejected input produces no payload: the bytes written before the error are a
        // frame short of the rest, as Go has always returned nothing.
        return new EncodeResult(errors.isEmpty() ? out.toArray() : EMPTY, warnings, errors);
    }

    /** PS-551: what every encoder says for a required value the input omits. */
    static String missingFieldMessage(String name) {
        return "Missing required field: " + name;
    }

    /**
     * The reference's {@code declared_sources}: every name a field list declares, anywhere
     * in it, with whether some declaration reads payload bytes or computes from them, and
     * the values its literals give. For the empty-payload check (PS-442).
     */
    static final class Source {
        boolean reads;
        final List<Object> literals = new ArrayList<>();
    }

    static Map<String, Source> declaredSources(List<Field> list) {
        Map<String, Source> found = new LinkedHashMap<>();
        declare(list, found);
        return found;
    }

    private static void declare(List<Field> list, Map<String, Source> found) {
        if (list == null) return;
        for (Field f : list) {
            if (f == null) continue;
            String name = f.getName();
            if (name != null && !name.isEmpty()) {
                Source src = found.computeIfAbsent(name, k -> new Source());
                if (isLiteral(f)) src.literals.add(f.getValue()); else src.reads = true;
            }
            declare(f.getFields(), found);
            declare(f.getByteGroup(), found);
            declare(f.getTrailer(), found);
            declare(f.getTagFields(), found);
            if (f.getRecords() != null) for (List<Field> r : f.getRecords()) declare(r, found);
            if (f.getFlagged() != null && f.getFlagged().getGroups() != null) {
                for (Field.FlaggedGroup g : f.getFlagged().getGroups()) declare(g.getFields(), found);
            }
            declareMatch(f.getMatchInline(), found);
            if (f.getCases() != null) declareMatch(f, found);
            if (f.getTlvInline() != null) declare(List.of(f.getTlvInline()), found);
            if (f.getTlvCases() != null) for (List<Field> c : f.getTlvCases().values()) declare(c, found);
        }
    }

    @SuppressWarnings("unchecked")
    private static void declareMatch(Field match, Map<String, Source> found) {
        if (match == null) return;
        if (match.getName() != null && !match.getName().isEmpty()) {
            found.computeIfAbsent(match.getName(), k -> new Source()).reads = true;
        }
        if (match.getCases() != null) for (Field.Case c : match.getCases()) declare(c.getFields(), found);
        if (match.getMatchDefault() instanceof List<?> d) declare((List<Field>) d, found);
    }

    /**
     * The reference's {@code empty_payload_error} (PS-442): an encoder that wrote no
     * bytes, where the input gives a value only a field reading payload bytes (or
     * computing from them) reports, dropped that field's bytes without a word. A value
     * written in no bytes is not one: an empty list, mapping or string, a value a literal
     * of that name gives, an internal or absent one.
     */
    static String emptyPayloadError(List<Field> list, Map<String, Object> data) {
        Map<String, Source> sources = null;
        for (Map.Entry<String, Object> e : data.entrySet()) {
            String key = e.getKey();
            Object value = e.getValue();
            if (key == null || key.startsWith("_") || value == null) continue;
            if (value instanceof String t && t.isEmpty()) continue;
            if (value instanceof List<?> l && l.isEmpty()) continue;
            if (value instanceof Map<?, ?> m && m.isEmpty()) continue;
            if (value instanceof byte[] b && b.length == 0) continue;
            if (sources == null) sources = declaredSources(list);
            Source src = sources.get(key);
            if (src == null || !src.reads) continue;
            boolean literal = false;
            for (Object l : src.literals) literal |= Schema.sameLabel(l, value);
            if (literal) continue;
            return "Encoding wrote no bytes, though the input gives '" + key
                    + "', which the schema reads from the payload (PS-442)";
        }
        return null;
    }

    /** A claimable name: named, and not internal (literals and computed fields included). */
    private static String claimName(Field f) {
        String name = f.getName();
        return name == null || name.isEmpty() || name.startsWith("_") ? null : name;
    }

    /** A required value the input omits (PS-551), naming the field. */
    static final class MissingFieldException extends SchemaException.EncodeException {
        final String field;

        MissingFieldException(String field) {
            this(field, missingFieldMessage(field));
        }

        private MissingFieldException(String field, String message) {
            super(message);
            this.field = field;
        }

        /**
         * PS-574: a match whose case neither the input nor a decode record gives, and
         * which this encoder could not derive either, named by its discriminator.
         */
        static MissingFieldException undetermined(String field) {
            return new MissingFieldException(field, missingFieldMessage(field) + " (PS-574)");
        }
    }

    private static boolean isComputed(Field f) {
        return f.getType() == FieldType.NUMBER || f.getType() == FieldType.INTEGER;
    }

    private static boolean isLiteral(Field f) {
        return (f.getType() == FieldType.STRING || f.getType() == FieldType.BOOLEAN
                || isComputed(f)) && f.getValue() != null;
    }

    private static String describe(Field field) {
        if (field.getName() != null && !field.getName().isEmpty()) return field.getName();
        if (field.getTlvInline() != null || field.getType() == FieldType.TLV) return "tlv";
        if (field.getMatchInline() != null) return "match";
        if (field.getByteGroup() != null) return "byte_group";
        if (field.getFlagged() != null) return "flagged group";
        return String.valueOf(field.getType());
    }

    // --- field dispatch ------------------------------------------------------------

    /**
     * Encode one entry of a field list. Shared by the top-level loop and by the bodies of
     * the constructs, so a case body may itself hold a construct.
     *
     * @param siblings the field list this entry belongs to, where a repeat's
     *                 {@code count: $n} or {@code byte_length: $n} is found
     */
    private byte[] encodeOne(Field field, Map<String, Object> data, List<Field> siblings) {
        if (field.getTlvInline() != null) {
            return encodeTLV(field.getTlvInline(), data);
        }
        if (field.getMatchInline() != null) {
            return encodeMatch(field.getMatchInline(), data, field.getRaw(), siblings);
        }
        if (field.getByteGroup() != null) {
            return encodeByteGroup(field, data);
        }
        if (field.getFlagged() != null) {
            return encodeFlagged(field.getFlagged(), data);
        }

        FieldType type = field.getType();

        if (type == FieldType.REPEAT) {
            return encodeRepeat(field, data, siblings);
        }
        if (type == FieldType.OBJECT) {
            // A nested object is reported under its own name (PS-139), so its members
            // are read from that mapping. They were looked up in the enclosing data,
            // where they are not, and every member encoded as a zero.
            // A `name_from` object was reported under its templated key, so that is the
            // key to read it back from (CR-2026-030), as for any other field.
            String key = resolveEncodeName(field, data);
            if (key == null) key = field.getName();
            Object nested = data.get(key);
            if (!(nested instanceof Map<?, ?> m)) {
                // PS-551: a whole object the input omits. Its members were written as
                // zeros - looked up in the enclosing data, where they are not.
                throw new MissingFieldException(key);
            }
            return encodeObjectMembers(field.getFields(), stringKeyed(m));
        }
        if (isComputed(field)) {
            // Derived from other fields: no bytes of its own.
            return EMPTY;
        }
        if (field.isEcho()) {
            // PS-589 (CR-2026-175): a byte string that does not consume writes nothing; the
            // field lists record a value given for it (FrameBuffer.echo).
            return EMPTY;
        }
        if (type == FieldType.SKIP) {
            // `remaining` gives no count to pad on encode (PS-014).
            int length = field.getLength() > 0 ? field.getLength() : 0;
            return padding(length);                     // CR-2026-141: no value
        }

        // PS-359: a literal writes no bytes and its key is not required of the input.
        if ((type == FieldType.STRING || type == FieldType.BOOLEAN) && field.getValue() != null) {
            return EMPTY;
        }
        // PS-360: `value` on a field that reads bytes is the constant to write, whatever
        // the input supplies.
        if (field.getValue() != null) {
            return encodeField(field, field.getValue());
        }

        String name = field.getName() == null ? "" : field.getName();

        // A nameless field other than a skip is refused at load (PS-553, CR-2026-138);
        // it was written here as zero padding.
        Object value;
        if (name.startsWith("_")) {
            // PS-434: an internal field that reads bytes writes its value, else the
            // input's, else it is an error naming the field. It wrote 0.
            value = Wave6b.internalEncodeValue(field, data);
        } else {
            // `name_from` reports the value under a templated key, so that is the key to
            // read it back from (CR-2026-030).
            String lookupName = resolveEncodeName(field, data);
            if (lookupName == null) {
                throw new SchemaException.EncodeException("name_from '"
                        + field.getNameFrom() + "' references a field the data does not "
                        + "carry, so its output key cannot be rebuilt");
            }
            if (data.get(lookupName) != null) {
                value = data.get(lookupName);
            } else if (Wave6b.hasFieldDefault(field)) {
                value = Wave6b.fieldDefault(field);   // PS-554: a declared default, as input
            } else if (field.getSentinel() != null && !field.getSentinel().isEmpty()) {
                // A reading the decode omitted as "no reading" (PS-427) goes back as the
                // sentinel it was, not as zero: the raw value, with no modifier reversed.
                return encodeSentinel(field);
            } else {
                // PS-551: rejected, naming the field. This warned "Missing field" at the
                // top level, and wrote a zero there and silently everywhere else.
                throw new MissingFieldException(lookupName);
            }
        }

        if (type == FieldType.BITFIELD_STRING) {
            return encodeBitfieldString(field, String.valueOf(value));
        }
        return encodeField(field, reverseModifiers(value, field));
    }


    /**
     * The output key a {@code name_from} field was reported under, resolved from the data
     * rather than from decode variables.
     *
     * <p>Encoding is handed the decoded output, keyed by field name, and has no variables
     * to resolve a template against. So a {@code ${ref}} is looked for in the data first,
     * and failing that through a top-level field declaring {@code var: ref} - whose name
     * is often not the variable's (PS-267).
     *
     * <p>Without this the encoder looked the value up under the schema-declared name,
     * found nothing, warned about a key the schema never reports, and wrote a zero:
     * name-from.yaml re-encoded {@code 032a} as {@code 0300} (CR-2026-030).
     *
     * @return the resolved key, or null where the template cannot be rebuilt
     */
    private String resolveEncodeName(Field field, Map<String, Object> data) {
        String template = field.getNameFrom();
        if (template == null || template.isEmpty()) {
            return field.getName();
        }
        StringBuilder out = new StringBuilder();
        java.util.regex.Matcher matcher =
                java.util.regex.Pattern.compile("\\$\\{(\\w+)}").matcher(template);
        int last = 0;
        while (matcher.find()) {
            out.append(template, last, matcher.start());
            String reference = matcher.group(1);
            Object value = data.get(reference);
            if (value == null) {
                for (Field sibling : fields) {
                    if (reference.equals(sibling.getVar()) && sibling.getName() != null) {
                        value = data.get(sibling.getName());
                        break;
                    }
                }
            }
            if (value == null) {
                return null;
            }
            // The data carries what the field reports, its label included (PS-532).
            String text = Wave5.renderTemplateValue(value);
            if (text == null) {
                throw new SchemaException.EncodeException(
                        Wave5.templateShapeError(template, reference));
            }
            out.append(text);
            last = matcher.end();
        }
        out.append(template.substring(last));
        return out.toString();
    }

    /** {@link #resolveEncodeName}, or null where it cannot be resolved. */
    private String resolveEncodeNameOrNull(Field field, Map<String, Object> data) {
        try {
            return resolveEncodeName(field, data);
        } catch (RuntimeException e) {
            return null;
        }
    }

    /**
     * The first sentinel, as the raw bits the decoder compared it with (PS-427): never
     * through an {@code encoding}, which would write gray(255) = 0x80 for a sentinel of
     * 0xFF, and with no modifier reversed.
     */
    private byte[] encodeSentinel(Field field) {
        long sentinel = field.getSentinel().get(0);
        FieldType type = field.getType();
        if (field.getEncoding() != null && type.isInteger()) {
            boolean useLittle = "little".equalsIgnoreCase(field.getEffectiveEndian(schema.getEndian()));
            return writeInt(sentinel, type.defaultLength(), type.isSigned(), useLittle);
        }
        return encodeField(field, sentinel);
    }

    /** Encode a list of fields - a TLV case's value bytes, a match case's body. */
    private byte[] encodeFieldList(List<Field> list, Map<String, Object> data) {
        if (list == null || list.isEmpty()) return EMPTY;
        return encodeWithBitfieldRuns(list, deriveInputs(list, data), false);
    }

    // --- constructs ----------------------------------------------------------------

    private static boolean groupHasData(Field.FlaggedGroup group, Map<String, Object> data) {
        for (Field gf : group.getFields()) {
            if (gf.getName() != null && data.containsKey(gf.getName())) {
                return true;
            }
        }
        // A construct in the group - a nested flagged block, a byte_group - carries its
        // names in its own fields.
        for (Field f : claimableFields(group.getFields())) {
            if (data.containsKey(f.getName())) return true;
        }
        return false;
    }

    /**
     * Encode the groups whose fields the data carries; the absent ones cost no bytes.
     *
     * <p>CR-2026-136: the given groups' named members are one field list, laid out as a
     * decoder reads them, so a bool or bit range in a group is its bits of a shared byte
     * (vobo's digital groups) and a peek at the end of the block stays pending for the
     * field after it. Each group used to be its own list, written whole. A member with no
     * name is taken only where it is a construct in its group, as the reference takes it.
     */
    private byte[] encodeFlagged(Field.FlaggedDef fd, Map<String, Object> data) {
        List<Field> members = new ArrayList<>();
        Long recorded = recordedFlags(fd);              // PS-573: the groups its flags set
        for (Field.FlaggedGroup group : fd.getGroups()) {
            if (recorded != null ? ((recorded >> group.getBit()) & 1) == 0
                                 : !groupHasData(group, data)) continue;
            boolean hasConstruct = false;
            for (Field gf : group.getFields()) {
                if (gf.getName() == null || gf.getName().isEmpty()) hasConstruct = true;
            }
            if (hasConstruct) {
                // A construct in the group - a nested flagged block, a byte_group - is
                // encoded as any field list is, so the whole group joins the list. It was
                // passed over as nameless, so a nested group's values were dropped.
                members.addAll(group.getFields());
                continue;
            }
            for (Field gf : group.getFields()) {
                String name = gf.getName();
                if (name == null || name.isEmpty()) continue;
                if (isComputed(gf)) continue;
                members.add(gf);
            }
        }
        // A given group's members are each required (PS-551): encodeOne rejects one the
        // input omits, which was written as zero. They are a field list like any other, so
        // a nested block's mask is derived (deriveInputs) and a run of bit ranges is packed.
        return encodeWithBitfieldRuns(members, deriveInputs(members, data), false);
    }

    /**
     * Whether a plain field reads a bit range out of a byte it may share with others.
     *
     * <p>A {@code byte_group} member is excluded: that construct packs its own.
     */
    private static boolean isBareBitfield(Field field) {
        return field != null && field.getBits() > 0
                && (field.getByteGroup() == null || field.getByteGroup().isEmpty());
    }

    /**
     * Encode a field list, packing each run of bit ranges into the byte or bytes it
     * shares (CR-2026-024).
     *
     * <p>Encoding wrote each bit range as a whole byte holding its unshifted value,
     * ignoring the range and {@code consume: 0} alike, so a LoRaWAN MHDR's three ranges
     * came back as three bytes: {@code 40} encoded as {@code 020000}, the wrong length and
     * the wrong bits, with no error. {@code byte_group} was given packing when its own
     * encoding was fixed; a bare run - the same thing without the wrapper - never was, and
     * neither was a {@code flagged} group made of them. CR-2026-023 fixed the Python
     * reference encoder; this is the same fix here.
     *
     * <p>A run ends at the field whose {@code consume} closes the span, which is where
     * decoding stops reading from the same offset.
     */
    private byte[] encodeWithBitfieldRuns(List<Field> list, Map<String, Object> data,
                                          boolean topLevel) {
        Object[] entry = {list, null};                                           // CR-2026-165
        enclosing.add(entry);
        try {
            return encodeWithBitfieldRunsIn(list, data, topLevel, entry);
        } finally {
            enclosing.remove(enclosing.size() - 1);
        }
    }

    /**
     * The field lists enclosing the entry being encoded, within the current reported
     * object, each with its current entry (CR-2026-165: the names declared outside a
     * recorded match). Reset for an object and for each repeat element.
     */
    private List<Object[]> enclosing = new ArrayList<>();

    /** An object's members or a repeat element's fields: a new reported object. */
    private byte[] encodeObjectMembers(List<Field> list, Map<String, Object> data) {
        List<Object[]> saved = enclosing;
        enclosing = new ArrayList<>();
        try {
            return encodeFieldList(list, data);
        } finally {
            enclosing = saved;
        }
    }

    private byte[] encodeWithBitfieldRunsIn(List<Field> list, Map<String, Object> data,
                                            boolean topLevel, Object[] entry) {
        // CR-2026-136: laid out as the decoder reads it. A range or bool that does not
        // consume leaves its bytes pending, and the next field's bytes are ORed into them
        // rather than written after them.
        FrameBuffer out = new FrameBuffer();
        TailLevel tails = new TailLevel(out);                                    // PS-564
        List<Field> run = new ArrayList<>();
        String omittedOptional = null;      // the first optional field the input left out
        for (Field field : list) {
            if (!isBareBitfield(field)) {
                entry[1] = field;
                tails.start();
                flushRun(out, run, data, topLevel);
                tails.start();
                // PS-405: an optional field is written only where the input has it, and
                // supplying one after an omitted earlier one is an error: the decoder
                // would read it into the earlier field's place.
                String name = field.getName();
                if (field.isOptional() && name != null && !name.isEmpty() && !name.startsWith("_")) {
                    String key = resolveEncodeName(field, data);
                    if (key == null || data.get(key) == null) {
                        if (omittedOptional == null) omittedOptional = name;
                        continue;
                    }
                    if (omittedOptional != null) {
                        String message = "optional field '" + name + "' is supplied while the earlier "
                                + "optional field '" + omittedOptional + "' is not (PS-405)";
                        if (!topLevel) throw new SchemaException.EncodeException(message);
                        errors.add("Error encoding " + name + ": " + message);
                        continue;
                    }
                }
                if (field.isEcho()) {
                    // PS-589 (CR-2026-175): a byte string that does not consume re-reads
                    // bytes the other fields write, so it writes none and is not required;
                    // a value given for it is checked where the frame closes.
                    try {
                        out.echo(field, data);
                    } catch (RuntimeException e) {
                        if (!topLevel) throw e;
                        errors.add("Error encoding " + describe(field) + ": " + e.getMessage());
                    }
                    continue;
                }
                byte[] savedPeek = peeked;
                peeked = out.pending();
                try {
                    if (isDerivableAbsentBool(field, data)) {
                        // PS-551 (CR-2026-141): an absent bool that does not consume is
                        // fixed by the fields that write its bit, so it writes none.
                        int bit = field.getBoolBit() >= 0 ? field.getBoolBit() : field.getBitOffset();
                        out.placeBits(1, false, Math.max(0, bit), 0, 0, 1, name, true);
                        continue;
                    }
                    byte[] bytes = encodeOne(field, data, list);
                    if (isPlacedBool(field)) {
                        // A bool is its `bit` of the byte it peeks, advancing only where it
                        // declares `consume` (PS-389, PS-066). It was written as a whole
                        // byte of 0 or 1.
                        int bit = field.getBoolBit() >= 0 ? field.getBoolBit() : field.getBitOffset();
                        boolean set = bytes.length > 0 && bytes[0] != 0;
                        out.placeBits(1, false, Math.max(0, bit), set ? 1 : 0, field.getConsume(),
                                1, name, false);
                    } else {
                        Integer tail = carriedTail.remove(bytes);
                        out.extend(bytes, tail == null ? 0 : tail, frameMeta.remove(bytes));
                    }
                } catch (RuntimeException e) {
                    if (!topLevel) throw e;
                    if (e instanceof MissingFieldException missing
                            && missing.field.equals(resolveEncodeNameOrNull(field, data))) {
                        // The field itself is missing: said as the reference says it.
                        errors.add(e.getMessage());
                    } else if (e instanceof SchemaException.FieldEncodeException) {
                        errors.add(e.getMessage());
                    } else {
                        errors.add("Error encoding " + describe(field) + ": " + e.getMessage());
                    }
                } finally {
                    peeked = savedPeek;
                }
                continue;
            }
            run.add(field);
            // `consume` of 1 or more closes the span, which is where the run ends.
            if (field.getConsume() >= 1) {
                tails.start();
                flushRun(out, run, data, topLevel);
            }
        }
        tails.start();
        flushRun(out, run, data, topLevel);
        tails.close();
        int tail = out.pendingLength();
        FrameMeta meta = out.meta();
        byte[] frame = carrying(out.frame(), tail);
        if (frame.length > 0 || !meta.echoes().isEmpty()) frameMeta.put(frame, meta);
        return frame;
    }

    /**
     * CR-2026-141 (PS-551): a named bool that does not consume and that the input omits -
     * the fields after it fix its bit, so it is derived and writes none.
     */
    private boolean isDerivableAbsentBool(Field field, Map<String, Object> data) {
        if (!isPlacedBool(field) || field.getConsume() > 0 || field.getValue() != null) return false;
        String name = field.getName();
        if (name == null || name.isEmpty() || name.startsWith("_")) return false;
        if (field.getSentinel() != null && !field.getSentinel().isEmpty()) return false;
        String key = resolveEncodeNameOrNull(field, data);
        return key != null && data.get(key) == null;
    }

    /** {@code is_peek_field}: a named bit range or bool that does not consume. */
    private static boolean isPeekField(Field f) {
        if (f == null || f.getName() == null || f.getName().isEmpty() || f.getConsume() > 0) {
            return false;
        }
        return isPlacedBool(f) || isBareBitfield(f);
    }

    /**
     * {@code covered_peeks}: the peeks in {@code fields} a whole field after them writes
     * over - read from the schema, so a tlv case can be chosen without them (PS-551,
     * CR-2026-141). A peek counts only where the next field that reads bytes is a plain
     * whole field: a range that consumes, a skip or a construct ends its span first, and
     * its bits may be left unwritten (wt303's status nibble beside a mode nibble).
     */
    private static Set<Field> coveredPeeks(List<Field> fields) {
        Set<Field> out = java.util.Collections.newSetFromMap(new java.util.IdentityHashMap<>());
        List<Field> pending = new ArrayList<>();
        if (fields == null) return out;
        for (Field f : fields) {
            if (f == null) continue;
            if (isPeekField(f)) {
                pending.add(f);
                continue;
            }
            if (isComputed(f) || isLiteral(f)) continue;
            boolean construct = f.getMatchInline() != null || f.getTlvInline() != null
                    || (f.getByteGroup() != null && !f.getByteGroup().isEmpty())
                    || f.getFlagged() != null;
            FieldType t = f.getType();
            if (!construct && f.getBits() <= 0 && t != FieldType.SKIP && t != FieldType.OBJECT
                    && t != FieldType.REPEAT && t != FieldType.BOOL && t != FieldType.TLV) {
                out.addAll(pending);
            }
            pending.clear();
        }
        return out;
    }

    /**
     * A skip's zeros (CR-2026-141). Padding carries no value, so its bits fix no peek and
     * contradict none: a decoder skips them, whatever the peek before set there.
     */
    private byte[] padding(int length) {
        byte[] zeros = new byte[length];
        if (length > 0) frameMeta.put(zeros, new FrameMeta(new byte[length], List.of()));
        return zeros;
    }

    // --- CR-2026-141 (PS-551): the bits each field writes, and the peeks to check ---------

    /** A bit range or bool placed: raw is null where it was derived. */
    private record Peek(int pos, int size, boolean little, int start, int width, Long raw,
                        String name) {
        Peek shifted(int by) {
            return new Peek(pos + by, size, little, start, width, raw, name);
        }

        long mask() {
            return width >= 64 ? -1L : (1L << width) - 1;
        }

        /** The range's bits as {@code frame} holds them, past its end as zeros. */
        long found(byte[] frame) {
            long base = 0;
            for (int i = 0; i < size; i++) {
                int at = pos + (little ? size - 1 - i : i);
                base = (base << 8) | (at < frame.length ? frame[at] & 0xFF : 0);
            }
            return (base >>> start) & mask();
        }

        /** Byte {@code i} of the range's mask, in frame order. */
        int maskByte(int i) {
            long bits = mask() << start;
            int shift = (little ? i : size - 1 - i) * 8;
            return (int) ((bits >>> shift) & 0xFF);
        }
    }

    /** Per byte, the bits some field wrote (null: all), and the peeks placed. */
    private record FrameMeta(byte[] covered, List<Peek> peeks, List<Echo> echoes) {
        FrameMeta(byte[] covered, List<Peek> peeks) { this(covered, peeks, List.of()); }
    }

    /**
     * CR-2026-175 (PS-589): a non-consuming byte string given a value - where a decoder
     * would read it in the frame, and its length from the input, null for `remaining`.
     */
    private record Echo(int pos, Field field, Object given, Integer length) {
        Echo shifted(int by) { return new Echo(pos + by, field, given, length); }
    }

    /** The byte count of a non-consuming byte string, read from the input. */
    private static Integer echoLength(Field field, Map<String, Object> data) {
        if (field.getLengthRef() != null) {
            String ref = field.getLengthRef();
            if (ref.startsWith("$")) ref = ref.substring(1);
            Object v = data.get(ref);
            if (!(v instanceof Number n) || v instanceof Boolean || n.doubleValue() < 0
                    || n.doubleValue() != Math.rint(n.doubleValue())) {
                throw new MissingFieldException(ref);
            }
            return n.intValue();
        }
        if (field.getLength() < 0) return null;
        return field.getEffectiveLength();
    }

    /** A value as a PS-589 message writes it (the reference's {@code echo_text}). */
    private static String echoText(Object value) {
        if (value instanceof List<?> list) {
            List<String> parts = new ArrayList<>();
            for (Object v : list) {
                String t = echoText(v);
                if (t.startsWith("'") && t.endsWith("'") && t.length() >= 2) {
                    t = t.substring(1, t.length() - 1);
                }
                parts.add(t);
            }
            return "'[" + String.join(", ", parts) + "]'";
        }
        if (value instanceof Boolean b) return b ? "true" : "false";
        if (value instanceof Number) return jsNumber(value);
        return "'" + value + "'";
    }

    private static boolean echoEquals(Object a, Object b) {
        if (a instanceof Number x && b instanceof Number y && !(a instanceof Boolean)
                && !(b instanceof Boolean)) {
            return x.doubleValue() == y.doubleValue();
        }
        if (a instanceof List<?> la && b instanceof List<?> lb) {
            if (la.size() != lb.size()) return false;
            for (int i = 0; i < la.size(); i++) if (!echoEquals(la.get(i), lb.get(i))) return false;
            return true;
        }
        return java.util.Objects.equals(a, b);
    }

    /**
     * PS-589, where a frame closes: a value given for a non-consuming byte string equals
     * what the field decodes from the bytes the other fields wrote over its span - its
     * format and separator, or PS-534's trailing 0x00 removal, applied first.
     */
    private void checkEchoes(byte[] frame, List<Echo> echoes) {
        for (Echo e : echoes) {
            int length = e.length() == null ? Math.max(0, frame.length - e.pos()) : e.length();
            String written;
            if (e.pos() + length > frame.length) {
                written = "too few bytes";
            } else {
                Object found;
                try {
                    found = schema.echoValue(e.field(),
                            java.util.Arrays.copyOfRange(frame, e.pos(), e.pos() + length));
                } catch (RuntimeException x) {
                    found = null;
                }
                if (found != null && echoEquals(found, e.given())) continue;
                written = found != null ? echoText(found) : "too few bytes";
            }
            throw new SchemaException.EncodeException("Field '" + e.field().getName()
                    + "' is given " + echoText(e.given()) + ", but the other fields write "
                    + written + " over its span (PS-589)");
        }
    }

    /**
     * Each nested list's coverage and peeks, keyed by the array it returned, as
     * {@link #carriedTail} keys its pending tail. An array with no entry was written
     * whole by one field.
     */
    private final Map<byte[], FrameMeta> frameMeta = new java.util.IdentityHashMap<>();

    static String peekDisagreement(List<Peek> peeks, byte[] frame) {
        for (Peek p : peeks) {
            if (p.raw() == null || p.pos() + p.size() > frame.length) continue;
            long found = p.found(frame);
            if (found != p.raw()) {
                return "Field '" + p.name() + "' is given " + Long.toUnsignedString(p.raw())
                        + ", but the fields that share its bits write "
                        + Long.toUnsignedString(found) + " (PS-551)";
            }
        }
        return "A field writes bits a peek before it set otherwise (PS-551)";
    }

    /**
     * {@code check_peeks}, where a frame closes: every bit range and bool reads back what
     * it was given, and a derived one has every bit written by another field.
     */
    private void checkPeeks(byte[] frame) {
        FrameMeta meta = frameMeta.remove(frame);
        if (meta == null) return;
        checkBitPeeks(frame, meta);
        checkEchoes(frame, meta.echoes());                  // PS-589 (CR-2026-175)
    }

    private void checkBitPeeks(byte[] frame, FrameMeta meta) {
        for (Peek p : meta.peeks()) {
            if (p.raw() == null) {
                for (int i = 0; i < p.size(); i++) {
                    int at = p.pos() + i;
                    int have = at >= frame.length ? 0
                            : meta.covered() == null ? 0xFF : meta.covered()[at] & 0xFF;
                    int need = p.maskByte(i);
                    if ((have & need) != need) throw new MissingFieldException(p.name());
                }
                continue;
            }
            if (p.found(frame) != p.raw()) {
                throw new SchemaException.EncodeException(peekDisagreement(List.of(p), frame));
            }
        }
    }

    /** {@code body}'s coverage and peeks for {@code result}, which is a prefix then it. */
    private byte[] prefixed(int prefix, byte[] body, byte[] result) {
        FrameMeta meta = frameMeta.remove(body);
        if (meta == null || result.length == 0) return result;
        byte[] covered = new byte[result.length];
        java.util.Arrays.fill(covered, 0, Math.min(prefix, covered.length), (byte) 0xFF);
        for (int i = 0; i < body.length && prefix + i < covered.length; i++) {
            covered[prefix + i] = meta.covered() == null ? (byte) 0xFF : meta.covered()[i];
        }
        List<Peek> peeks = new ArrayList<>();
        for (Peek p : meta.peeks()) peeks.add(p.shifted(prefix));
        List<Echo> echoes = new ArrayList<>();
        for (Echo e : meta.echoes()) echoes.add(e.shifted(prefix));
        frameMeta.put(result, new FrameMeta(covered, peeks, echoes));
        return result;
    }

    /** A bool outside a byte_group: placed as its bit, not written as a byte. */
    private static boolean isPlacedBool(Field field) {
        return field.getType() == FieldType.BOOL
                && (field.getByteGroup() == null || field.getByteGroup().isEmpty())
                && field.getMatchInline() == null && field.getFlagged() == null
                && field.getTlvInline() == null;
    }

    /** The bytes a bit range before the current field peeked and filled, if any. */
    private byte[] peeked;

    /**
     * CR-2026-136: how many trailing bytes of a nested field list's result are still
     * pending - peeked by a bit range or bool and consumed by nothing in that list. The
     * enclosing list keeps them pending, so the field after the construct shares them, as
     * a decoder reads it. Keyed by the array's identity, so a construct returning a nested
     * list's bytes unchanged carries its tail with no extra plumbing.
     */
    private final Map<byte[], Integer> carriedTail = new java.util.IdentityHashMap<>();

    private byte[] carrying(byte[] bytes, int tail) {
        if (tail > 0 && bytes.length > 0) carriedTail.put(bytes, Math.min(tail, bytes.length));
        return bytes;
    }

    /** Emit a pending run, if any, and clear it. */
    private void flushRun(FrameBuffer out, List<Field> run, Map<String, Object> data,
                          boolean topLevel) {
        if (run.isEmpty()) return;
        try {
            encodeBitfieldRun(run, data, out);
        } catch (RuntimeException e) {
            if (!topLevel) {
                run.clear();
                throw e;
            }
            List<String> names = new ArrayList<>();
            for (Field field : run) names.add(String.valueOf(field.getName()));
            errors.add("Error encoding bit range(s) " + String.join(", ", names)
                    + ": " + e.getMessage());
        }
        run.clear();
    }

    /**
     * Place one run of bit ranges into the byte or bytes they share. Each range is ORed
     * into the frame's pending base and advances only by its own {@code consume}
     * (CR-2026-136, PS-016), so bytes a range peeks are shared with the field that comes
     * next, as a decoder reads them. The run used to be written whole, and the next
     * field's bytes after it.
     */
    private void encodeBitfieldRun(List<Field> run, Map<String, Object> data, FrameBuffer out) {
        // The run's shared base is in its fields' effective byte order (PS-059): a field's
        // own `endian` where one declares it, else the schema's.
        String declared = run.stream().map(Field::getEndian).filter(e -> e != null).findFirst().orElse(null);
        boolean runLittle = declared != null ? "little".equals(declared) : little;
        for (Field member : run) {
            String name = member.getName() == null ? "" : member.getName();
            // A nameless range is refused at load (PS-553); it was written as zero bits.
            Object value;
            if (member.getValue() != null || name.startsWith("_")) {
                value = Wave6b.internalEncodeValue(member, data);   // PS-360, PS-434
            } else if (data.get(name) == null && Wave6b.hasFieldDefault(member)) {
                value = Wave6b.fieldDefault(member);                // PS-554
            } else if (data.get(name) == null) {
                if (member.getConsume() == 0) {
                    // PS-551 (CR-2026-141): a range that does not consume is fixed by the
                    // fields that write its bits, so it is derived and writes none.
                    out.placeBits(Math.max(1, member.getBitBaseBytes()), runLittle,
                            member.getBitOffset(), 0, 0, member.getBits(), name, true);
                    continue;
                }
                // PS-551. A range was written as zero bits, which read back as a value.
                throw new MissingFieldException(name);
            } else {
                value = data.get(name);
            }
            value = reverseModifiers(value, member);
            Long raw = asLong(value);
            if (raw == null) {
                // A label on a bit range: recover the number rather than writing zero,
                // which would be the silent wrong answer this fix exists to remove.
                raw = bitfieldLabelValue(value, member);
            }
            // PS-064 before PS-062: a value the range cannot hold is rejected, never masked
            // into a neighbour's bits; the mask only keeps a signed value within the width.
            checkBitRange(raw, member);                                  // PS-168, PS-552
            long mask = member.getBits() >= 64 ? -1L : (1L << member.getBits()) - 1;
            out.placeBits(Math.max(1, member.getBitBaseBytes()), runLittle,
                    member.getBitOffset(), raw & mask, member.getConsume(),
                    member.getBits(), name, false);
        }
    }

    /** The number a bit range's {@code enum} or {@code values} label stands for. */
    private static Long bitfieldLabelValue(Object value, Field member) {
        String label = String.valueOf(value);
        Map<?, ?> table = member.getValues();
        if (table != null) {
            for (Map.Entry<?, ?> entry : table.entrySet()) {
                if (String.valueOf(entry.getValue()).equals(label)) {
                    return Long.parseLong(String.valueOf(entry.getKey()));
                }
            }
        }
        throw new SchemaException.EncodeException("bit range '" + member.getName()
                + "': '" + label + "' is not one of its declared values");
    }

    /**
     * Pack a {@code byte_group}'s bit ranges back into their shared byte or bytes.
     *
     * <p>Without this the construct falls through to the plain field path, which finds no
     * name and emits a single zero byte: the right length, the wrong bits, and no error to
     * say so.
     */
    private byte[] encodeByteGroup(Field group, Map<String, Object> data) {
        long packed = 0;
        int size = Math.max(1, group.getByteGroupSize());

        for (Field member : group.getByteGroup()) {
            String name = member.getName();
            if (isComputed(member) || isLiteral(member)) continue;   // no bits of its own
            Object value;
            if (name == null || name.isEmpty()) {
                value = Long.valueOf(0);
            } else if (member.getValue() != null || name.startsWith("_")) {
                value = Wave6b.internalEncodeValue(member, data);   // PS-360, PS-434
            } else if (data.get(name) == null && Wave6b.hasFieldDefault(member)) {
                value = Wave6b.fieldDefault(member);                // PS-554
            } else if (data.get(name) == null) {
                // PS-551. A member was written as zero bits, which read back as a value.
                throw new MissingFieldException(name);
            } else {
                value = data.get(name);
            }
            if (member.getType() == FieldType.BOOL) {
                // A bool member is one bit of the group's value (PS-364).
                if (Boolean.TRUE.equals(value)) packed |= 1L << Math.max(0, member.getBoolBit());
                continue;
            }
            value = reverseModifiers(value, member);
            Long raw = asLong(value);
            if (raw == null) continue;
            if (member.getBits() > 0) {
                // The width is the group's (PS-070, CR-2026-131), set when it was parsed.
                checkBitRange(raw, member);                              // PS-168, PS-552
                long mask = member.getBits() >= 64 ? -1L : (1L << member.getBits()) - 1;
                packed |= (raw & mask) << member.getBitOffset();
            } else {
                // A whole integer is its full range (PS-070), masked like any other member.
                int bits = member.getType().defaultLength() * 8;
                long[] wb = integerBounds(bits, member.getType().isSigned());
                checkFits(raw, wb[0], wb[1], member.getName(), given(member, raw));   // PS-168
                packed |= bits >= 64 ? raw : raw & ((1L << bits) - 1);
            }
        }
        String groupEndian = group.getGroupEndian();
        return writeInt(packed, size, false, groupEndian != null ? "little".equals(groupEndian) : little);
    }

    /**
     * Encode a {@code repeat}: its records back to back, and nothing else.
     *
     * <p>The framing costs no bytes here. {@code count: $n} and {@code byte_length: $len}
     * name a field earlier in the list, which the main loop encodes from its own value,
     * and {@code until: end} needs no header at all.
     */
    private byte[] encodeRepeat(Field field, Map<String, Object> data, List<Field> siblings) {
        // PS-564 is not checked inside a tlv or repeat (tail_unchecked).
        tailDepth++;
        try {
            return encodeRepeatBody(field, data, siblings);
        } finally {
            tailDepth--;
        }
    }

    private byte[] encodeRepeatBody(Field field, Map<String, Object> data, List<Field> siblings) {
        String name = field.getName();
        Object records = name == null ? null : data.get(name);
        if (records != null && !(records instanceof Map<?, ?>) && !(records instanceof List<?>)) {
            throw new SchemaException.EncodeException("repeat field '" + name
                    + "': expected a list of records, got " + records.getClass().getSimpleName());
        }
        List<Object> list = repeatRecords(field, data);
        if (list == null) {
            // PS-551: an absent list is a missing value. It wrote nothing, leaving the frame
            // short of the elements a count or byte_length field still declared (PS-442).
            throw new MissingFieldException(name);
        }
        Long count = repeatSize(field, field.getCount(), data, siblings);
        ByteBuf out = new ByteBuf();
        out.write(encodeRepeatElements(field, data, list, count));
        Long byteLength = repeatSize(field, field.getByteLength(), data, siblings);
        if (byteLength != null && out.toArray().length != byteLength) {
            throw new SchemaException.EncodeException("Repeat '" + name + "' encodes to "
                    + out.toArray().length + " bytes for a byte_length of " + byteLength
                    + " (PS-551)");
        }
        // PS-385: the elements, then the trailer, whose values sit beside the repeat.
        if (field.getTrailer() != null && !field.getTrailer().isEmpty()) {
            byte[] trailer = encodeFieldList(field.getTrailer(), data);
            checkPeeks(trailer);                                         // PS-551
            out.write(trailer);
        }
        return out.toArray();
    }

    /** A repeat's list as the input gives it, or null where it gives none. */
    @SuppressWarnings("unchecked")
    private static List<Object> repeatRecords(Field field, Map<String, Object> data) {
        Object records = field.getName() == null ? null : data.get(field.getName());
        if (records instanceof Map<?, ?>) return List.of(records);
        if (records instanceof List<?> asList) return (List<Object>) asList;
        return null;
    }

    /** A repeat's {@code count} or {@code byte_length} as the schema or the input states it. */
    private static Long repeatSize(Field field, Object size, Map<String, Object> data,
                                   List<Field> siblings) {
        Object value = size;
        if (size instanceof String text && text.startsWith("$")) {
            Field source = sizeSource(siblings, text.substring(1));
            value = data.get(source != null ? source.getName() : text.substring(1));
        }
        if (value instanceof Boolean || !(value instanceof Number n)) return null;
        double d = n.doubleValue();
        if (d != Math.rint(d)) return null;
        return (long) d;
    }

    /**
     * A repeat's elements back to back: no framing, no trailer. {@code count}, where the
     * schema or the input states one, is checked after the refusals that concern the schema
     * rather than the input (PS-550, PS-542, PS-544, PS-387).
     */
    private byte[] encodeRepeatElements(Field field, Map<String, Object> data,
                                        List<Object> list, Long count) {
        // PS-564 is not checked inside a tlv or repeat (tail_unchecked).
        tailDepth++;
        try {
            return encodeRepeatElementsBody(field, data, list, count);
        } finally {
            tailDepth--;
        }
    }

    private byte[] encodeRepeatElementsBody(Field field, Map<String, Object> data,
                                            List<Object> list, Long count) {
        String name = field.getName();
        // PS-550: a sibling identifying these values position by position is given as many
        // values as this repeat, as a decoder requires (PS-546).
        if (field.getRaw() != null) {
            Set<String> seen = new LinkedHashSet<>();
            for (String[] ref : Wave6a.siblingReferences(field.getRaw())) {
                if (!seen.add(ref[1])) continue;
                Object given = data.get(ref[1]);
                if (!(given instanceof List<?> l) || l.size() != list.size()) {
                    Object have = given instanceof List<?> l2 ? (Object) l2.size() : "none";
                    throw new SchemaException.EncodeException("Repeat '" + name + "' is given "
                            + list.size() + " values and '" + ref[1] + "', which identifies them, "
                            + have + " (PS-550)");
                }
            }
        }
        if (field.getRaw() != null && Wave6a.recordsReachable(Wave6a.repeatMembers(field.getRaw()))) {
            // PS-542: the records do not say which element they came from.
            throw new SchemaException.EncodeException("Repeat '" + name + "' has elements that yield "
                    + "several records, which cannot be encoded (PS-542)");
        }
        if (field.getElementName() != null) {
            // PS-529: each value is encoded as the element, wrapped as it was decoded.
            // PS-544: a null is written as the element's first sentinel - an absent value,
            // which the field list writes as the sentinel's raw bits - and is an error
            // where the element declares none.
            Field element = field.getFields() == null || field.getFields().isEmpty()
                    ? null : field.getFields().get(0);
            boolean hasSentinel = element != null && element.getSentinel() != null
                    && !element.getSentinel().isEmpty();
            List<Object> wrapped = new ArrayList<>();
            for (int position = 0; position < list.size(); position++) {
                Object item = list.get(position);
                Map<String, Object> record = new LinkedHashMap<>();
                if (item == null) {
                    if (!hasSentinel) {
                        throw new SchemaException.EncodeException("Repeat '" + name + "' has null at position "
                                + position + ", and its element declares no `sentinel` to write for it (PS-544)");
                    }
                } else {
                    record.put(field.getElementName(), item);
                }
                wrapped.add(record);
            }
            list = wrapped;
        }

        // PS-387: an element present_if dropped is not in the data, and its bytes were in
        // the payload, so they cannot be written back. Only elements that read nothing
        // (computed fields and literals) can be encoded.
        if (field.getPresentIf() != null && field.getFields() != null) {
            for (Field member : field.getFields()) {
                boolean readsNothing = member.getType() == FieldType.NUMBER
                        || member.getType() == FieldType.BOOLEAN
                        || (member.getType() == FieldType.STRING && member.getValue() != null);
                if (!readsNothing) {
                    throw new SchemaException.EncodeException("repeat field '" + name
                            + "' declares present_if and its elements read payload bytes, so the "
                            + "elements it dropped cannot be encoded (PS-387)");
                }
            }
        }
        if (count != null && list.size() != count) {
            // PS-551: the count and the list must agree; nothing is padded or cut.
            throw new SchemaException.EncodeException("Repeat '" + name + "' is given "
                    + list.size() + " values for a count of " + count + " (PS-551)");
        }

        String index = field.getIndex();
        ByteBuf out = new ByteBuf();
        for (int position = 0; position < list.size(); position++) {
            Object record = list.get(position);
            if (!(record instanceof Map<?, ?> asMap)) {
                throw new SchemaException.EncodeException("repeat field '" + name
                        + "': expected each record to be a mapping, got "
                        + record.getClass().getSimpleName());
            }
            Map<String, Object> element = stringKeyed(asMap);
            if (index != null) {
                // PS-370: the index is bound while each element is encoded, as it was
                // while each was decoded, so a name_from template resolves the same way.
                element = new LinkedHashMap<>(element);
                element.put(index, (long) position + field.getIndexBase());   // PS-530
            }
            // PS-572: the element's position, as its decode recorded it.
            boolean pushed = rec != null && rec.push(field.getRaw(), "repeat", position);
            byte[] encoded;
            try {
                encoded = encodeObjectMembers(field.getFields(), element);
            } finally {
                if (rec != null) rec.pop(pushed);
            }
            checkPeeks(encoded);            // PS-551: an element's bytes close with it
            out.write(encoded);
        }
        return out.toArray();
    }

    // --- PS-551: what the input fixes ------------------------------------------------

    /**
     * The field of this list a {@code $ref} names, by its name or its {@code var:}.
     * Searched into a byte_group's members, since a count is often a few bits of a shared
     * byte, and into no other construct.
     */
    private static Field sizeSource(List<Field> fields, String ref) {
        if (fields == null) return null;
        for (Field f : fields) {
            if (f.getByteGroup() != null && !f.getByteGroup().isEmpty()) {
                Field found = sizeSource(f.getByteGroup(), ref);
                if (found != null) return found;
                continue;
            }
            if (f.getName() != null && !f.getName().isEmpty()
                    && (ref.equals(f.getName()) || ref.equals(f.getVar()))) {
                return f;
            }
        }
        return null;
    }

    /**
     * Whether a field's value can be derived and written as it stands: it reads bytes of
     * its own and reports them unchanged, with no arithmetic or lookup between the bytes
     * and the value a reference binds.
     */
    private static boolean derivable(Field source) {
        if (source == null || source.getName() == null || source.getName().isEmpty()) return false;
        if (isComputed(source) || isLiteral(source)) return false;
        return source.getValue() == null && source.getNameFrom() == null
                && (source.getLookup() == null || source.getLookup().isEmpty())
                && source.getMult() == null && source.getDiv() == null && source.getAdd() == null
                && (source.getTransform() == null || source.getTransform().isEmpty())
                && (source.getPolynomial() == null || source.getPolynomial().isEmpty())
                && source.getEncoding() == null && source.getCompute() == null
                && source.getRef() == null && source.getGuard() == null;
    }

    /**
     * Whether {@code source} is a bit range in a run nothing closes with {@code consume}.
     * Such a run reads bytes the fields after it read again, and the encoder writes them
     * twice, so a value derived into it would add bytes rather than fix them.
     */
    private static boolean isPeek(List<Field> fields, Field source) {
        List<Field> run = new ArrayList<>();
        for (Field field : fields) {
            if (!isBareBitfield(field)) {
                if (run.contains(source)) return run.get(run.size() - 1).getConsume() < 1;
                run.clear();
                continue;
            }
            run.add(field);
            if (field.getConsume() >= 1) {
                if (run.contains(source)) return false;
                run.clear();
            }
        }
        return run.contains(source) && run.get(run.size() - 1).getConsume() < 1;
    }

    /** The field a {@code $ref} names, where a value can be derived into it. */
    private static Field derivedSource(List<Field> fields, Object ref) {
        if (!(ref instanceof String text) || !text.startsWith("$")) return null;
        Field source = sizeSource(fields, text.substring(1));
        if (!derivable(source) || isPeek(fields, source)) return null;
        return source;
    }

    /** The one discriminator a match case key stands for, or null. */
    private static Long exactCaseValue(Object key) {
        if (key == null || key instanceof Boolean) return null;
        if (key instanceof Number n) {
            double d = n.doubleValue();
            return d == Math.rint(d) ? (long) d : null;
        }
        if (key instanceof String text) return parseIntAny(text);
        return null;
    }

    /** The reference's {@code _default_body}: a match's default field list, or null. */
    @SuppressWarnings("unchecked")
    private static List<Field> defaultBody(Field match) {
        if (match.getMatchDefault() instanceof List<?> d) return (List<Field>) d;
        if (match.getCases() != null) {
            for (Field.Case c : match.getCases()) {
                if (c.isDefault() && c.getFields() != null) return c.getFields();
            }
        }
        return null;
    }

    /**
     * The reference's {@code _body_score}: how many of a branch's reported names the input
     * gives, counted through a byte_group's or flagged group's members, a nested match's
     * best branch and a tlv's cases; or null where a literal the input contradicts rules
     * the branch out (PS-359, PS-513), as does a nested match every branch of which is
     * ruled out and whose default is not {@code skip}. A computed field is not counted.
     * Only the top level used to be counted, so a {@code remaining} match whose case holds
     * a header and a match on its type byte chose no case and wrote nothing.
     */
    private Integer bodyScore(List<Field> body, Map<String, Object> data) {
        if (body == null) return null;
        int score = 0;
        for (Field f : body) {
            if (f == null) continue;
            if (f.getByteGroup() != null && !f.getByteGroup().isEmpty()) {
                Integer h = bodyScore(f.getByteGroup(), data);
                if (h == null) return null;
                score += h;
                continue;
            }
            if (f.getFlagged() != null && f.getFlagged().getGroups() != null) {
                for (Field.FlaggedGroup g : f.getFlagged().getGroups()) {
                    Integer h = bodyScore(g.getFields(), data);
                    score += h == null ? 0 : h;
                }
                continue;
            }
            if (f.getMatchInline() != null) {
                Field m = f.getMatchInline();
                if (m.getName() != null && !m.getName().isEmpty() && data.containsKey(m.getName())) score++;
                List<List<Field>> bodies = new ArrayList<>();
                if (m.getCases() != null) {
                    for (Field.Case c : m.getCases()) {
                        if (!c.isDefault() && c.getFields() != null) bodies.add(c.getFields());
                    }
                }
                List<Field> d = defaultBody(m);
                if (d != null) bodies.add(d);
                Integer best = null;
                for (List<Field> b : bodies) {
                    Integer h = bodyScore(b, data);
                    if (h != null && (best == null || h > best)) best = h;
                }
                if (best != null) score += best;
                else if (!"skip".equals(m.getMatchDefault())) return null;
                continue;
            }
            Field tlv = f.getTlvInline() != null ? f.getTlvInline()
                    : (f.getType() == FieldType.TLV ? f : null);
            if (tlv != null && tlv.getTlvCases() != null) {
                Set<String> names = new LinkedHashSet<>();
                for (List<Field> c : tlv.getTlvCases().values()) {
                    List<Claim> claims = new ArrayList<>();
                    collectClaims(c, false, claims);
                    for (Claim cl : claims) names.add(cl.field().getName());
                }
                for (String n : names) if (data.containsKey(n)) score++;
                continue;
            }
            String name = claimName(f);
            if (name == null || (isComputed(f) && !isLiteral(f))) continue;
            String key = name;
            if (f.getNameFrom() != null && !f.getNameFrom().isEmpty()) key = resolveEncodeNameOrNull(f, data);
            if (key == null || !data.containsKey(key)) continue;
            if (isLiteral(f) && !Schema.sameLabel(f.getValue(), data.get(key))) return null;
            score++;
        }
        return score;
    }

    /** The default list's score for the input; 0 where there is none or it is ruled out. */
    private int defaultScore(Field match, Map<String, Object> data) {
        List<Field> d = defaultBody(match);
        Integer s = d == null ? null : bodyScore(d, data);
        return s == null ? 0 : s;
    }

    /** The reference's {@code _best_case}: the case scoring highest, and whether tied. */
    private record BestCase(Field.Case c, boolean tied) {}

    private BestCase bestCase(Field match, Map<String, Object> data) {
        Field.Case best = null;
        int bestHits = 0;
        boolean tied = false;
        if (match.getCases() != null) {
            for (Field.Case c : match.getCases()) {
                if (c.isDefault() || c.getFields() == null) continue;
                Integer s = bodyScore(c.getFields(), data);
                int hits = s == null ? 0 : s;
                if (hits > bestHits) {
                    best = c;
                    bestHits = hits;
                    tied = false;
                } else if (hits > 0 && hits == bestHits) {
                    tied = true;
                }
            }
        }
        if (best == null) return null;
        List<Field> d = defaultBody(match);
        if (d != null) {
            Integer s = bodyScore(d, data);
            if (s != null && s > bestHits) return null;
        }
        return new BestCase(best, tied);
    }

    /**
     * The discriminator of the one match case whose fields the input gives: null where no
     * case's fields are given, where two cases explain the input equally well, where the
     * default explains it better, or where the case stands for more than one value.
     */
    private Long soleCaseGiven(Field match, Map<String, Object> data) {
        BestCase best = bestCase(match, data);
        if (best == null || best.tied()) return null;
        return exactCaseValue(best.c().getCaseValue());
    }

    /**
     * PS-551: the values this field list's input omits but the rest of it fixes - a
     * repeat's {@code count: $n} or {@code byte_length: $n} field from the list given, a
     * match's {@code field: $d} discriminator from the one case whose fields are given, a
     * flagged construct's mask from the groups given. A supplied value is kept: the
     * construct checks it agrees, and the input is rejected where it does not. The input
     * itself is not modified.
     */
    private Map<String, Object> deriveInputs(List<Field> list, Map<String, Object> data) {
        Map<String, Object> derived = new LinkedHashMap<>(data);
        if (rec != null) recordedInputs(list, data, derived);                   // PS-573
        for (Field f : list) {
            String lengthRef = f.getLengthRef();
            if (Cr181.isByteString(f.getType()) && lengthRef != null && !lengthRef.isEmpty()
                    && data.get(f.getName()) != null) {
                // PS-602, PS-551 (CR-2026-181): the value's byte count fixes the field its
                // length names; a given value that differs is rejected.
                int count = Cr181.input(f, data.get(f.getName())).length;
                Field source = sizeSource(list, lengthRef);
                String name = source != null ? source.getName() : lengthRef;
                if (derived.get(name) == null) {
                    if (source != null && derivable(source) && !isPeek(list, source)) {
                        derived.put(name, (long) count);
                    }
                    continue;
                }
                Object given = boundFromReported(source, derived.get(name));
                if (given instanceof Number n && !(given instanceof Boolean) && n.doubleValue() != count) {
                    throw new SchemaException.FieldEncodeException("Field '" + f.getName() + "' is given "
                            + count + " bytes, but '" + name + "', its length, is " + messageValue(given)
                            + " (PS-602)");
                }
                continue;
            }
            if (f.getType() == FieldType.REPEAT && f.getMatchInline() == null) {
                List<Object> records = repeatRecords(f, data);
                if (records == null) continue;
                for (Object ref : new Object[]{f.getCount(), f.getByteLength()}) {
                    Field source = derivedSource(list, ref);
                    if (source == null || derived.get(source.getName()) != null) continue;
                    if (ref == f.getCount()) {
                        derived.put(source.getName(), (long) records.size());
                    } else {
                        derived.put(source.getName(),
                                (long) encodeRepeatElements(f, data, records, null).length);
                    }
                }
            } else if (f.getMatchInline() != null) {
                Field match = f.getMatchInline();
                Field source = derivedSource(list, match.getOn());
                if (source == null || derived.get(source.getName()) != null) continue;
                Long key = soleCaseGiven(match, data);
                if (key != null) derived.put(source.getName(), key);
            } else if (f.getFlagged() != null) {
                Field.FlaggedDef fd = f.getFlagged();
                String mask = fd.getField() == null ? "" : fd.getField();
                if (mask.startsWith("$")) mask = mask.substring(1);
                Field source = sizeSource(list, mask);
                String name = source != null ? source.getName() : mask;
                long bits = 0;
                long declared = 0;
                for (Field.FlaggedGroup group : fd.getGroups()) {
                    declared |= 1L << group.getBit();
                    if (groupHasData(group, data)) bits |= 1L << group.getBit();
                }
                Object given = data.get(name);
                Long recordedFlags = recordedFlags(fd);
                if (given == null && recordedFlags != null) {
                    // PS-573: the flags the decode record holds; the groups written are
                    // the ones they set (encodeFlagged), so there is nothing to check.
                    derived.putIfAbsent(name, recordedFlags);
                    continue;
                }
                if (given == null && derived.get(name) != null) {
                    // An internal mask the decode record supplied, checked as a supplied
                    // one is.
                    given = derived.get(name);
                }
                if (given == null) {
                    derived.put(name, bits);
                    continue;
                }
                // The groups test what a reference to the mask binds (PS-523,
                // CR-2026-132): an enum's code, the number under a text label. Compared as
                // reported, an enum name or a text label passed unchecked.
                given = boundFromReported(source, given);
                if (given instanceof Number n && !(given instanceof Boolean)
                        && n.doubleValue() == Math.rint(n.doubleValue())
                        && (n.longValue() & declared) != bits) {
                    throw new SchemaException.EncodeException("Flagged field '" + name
                            + "' is given " + n.longValue() + ", and the groups given set "
                            + bits + " (PS-551)");
                }
            }
        }
        return derived;
    }

    // --- PS-573: the decode record ----------------------------------------------------

    /**
     * What the decode record supplies for this field list (the reference's
     * {@code _recorded_inputs}): each internal field's recorded value where the input does
     * not give it, an input giving another value being rejected; and each flagged
     * construct's recorded flags under its flags field. A byte_group's members are looked
     * into, as the record locates them.
     */
    private void recordedInputs(List<Field> list, Map<String, Object> data,
                                Map<String, Object> derived) {
        for (Field f : list) {
            if (f.getByteGroup() != null) {
                for (Field member : f.getByteGroup()) recordedInternal(member, data, derived);
                continue;
            }
            if (f.getFlagged() != null) {
                String mask = f.getFlagged().getField() == null ? "" : f.getFlagged().getField();
                if (mask.startsWith("$")) mask = mask.substring(1);
                Object flags = rec.lookup(rec.flagged, f.getFlagged().getRaw());
                if (!mask.isEmpty() && flags != DecodeRecord.UNRECORDED
                        && data.get(mask) == null && derived.get(mask) == null) {
                    derived.put(mask, flags);
                }
                continue;
            }
            recordedInternal(f, data, derived);
        }
    }

    private void recordedInternal(Field f, Map<String, Object> data, Map<String, Object> derived) {
        String name = f.getName();
        if (name == null || !name.startsWith("_") || isComputed(f) || isLiteral(f)
                || f.getValue() != null) return;
        Object recorded = rec.lookup(rec.internal, f.getRaw());
        if (recorded == DecodeRecord.UNRECORDED) return;
        if (data.containsKey(name) && !recordSupplied.contains(name)) {
            if (!sameValue(data.get(name), recorded)) {
                throw new SchemaException.EncodeException("Field '" + name + "' is given "
                        + messageValue(data.get(name)) + ", but the decode record holds "
                        + messageValue(recorded) + " (PS-573)");
            }
            return;
        }
        derived.put(name, recorded);
        recordSupplied.add(name);
    }

    private static boolean sameValue(Object a, Object b) {
        if (a instanceof Number x && b instanceof Number y
                && !(a instanceof Boolean) && !(b instanceof Boolean)) {
            return x.doubleValue() == y.doubleValue();
        }
        return java.util.Objects.equals(a, b);
    }

    /** The flags the record holds for a flagged construct the input gives no flags for. */
    private Long recordedFlags(Field.FlaggedDef fd) {
        if (rec == null) return null;
        String mask = fd.getField() == null ? "" : fd.getField();
        if (mask.startsWith("$")) mask = mask.substring(1);
        if (inputNames.contains(mask)) return null;
        Object recorded = rec.lookup(rec.flagged, fd.getRaw());
        if (recorded instanceof Number n && !(recorded instanceof Boolean)) {
            return (long) Math.floor(n.doubleValue());
        }
        return null;
    }

    /** The case of a match a decode record names: a case, or "default". */
    private static Object recordedCase(Field match, Object key) {
        if ("default".equals(key)) return "default";
        for (Field.Case c : match.getCases() == null ? List.<Field.Case>of() : match.getCases()) {
            if (!c.isDefault() && String.valueOf(c.getCaseValue()).trim()
                    .equals(String.valueOf(key).trim())) return c;
        }
        throw new SchemaException.EncodeException("Field " + matchLabel(match) + ": the recorded case "
                + messageValue(key) + " is not a case of the match (PS-573)");
    }

    /** A value quoted in a PS-573 message (CR-2026-164): a number as JavaScript's
     * String(number), anything else as Python's repr. */
    static String messageValue(Object value) {
        if (value instanceof Number) return jsNumber(value);
        return Meta.pyRepr(value);
    }

    /** How a message names a match: its name, else its var, quoted; else 'an unnamed match'. */
    static String matchLabel(Field match) {
        String n = match.getName() != null && !match.getName().isEmpty() ? match.getName()
                : match.getVar() != null && !match.getVar().isEmpty() ? match.getVar() : null;
        return n != null ? "'" + n + "'" : "an unnamed match";
    }

    /**
     * PS-573: under a case taken from the decode record, the input may not give a field
     * only another case of the match declares ({@code chosen} null for the default).
     * A name something outside the match declares within the same reported object - the
     * other entries of the lists enclosing it - is not one (CR-2026-165). Its
     * {@code default} list is one of its cases (CR-2026-164).
     */
    private void checkRecordedCase(Field match, String chosen, Map<String, Object> data) {
        Map<?, ?> raw = match.getRaw();
        Map<?, ?> cases = raw != null && raw.get("cases") instanceof Map<?, ?> m ? m : Map.of();
        Object dflt = raw != null && raw.get("default") instanceof List<?> l ? l
                : cases.get("default") instanceof List<?> l2 ? l2 : null;
        Set<String> mine = new java.util.HashSet<>(recordDeclaredFields(
                chosen != null && cases.containsKey(chosen) ? cases.get(chosen) : dflt, null));
        java.util.TreeSet<String> others = new java.util.TreeSet<>();
        for (Map.Entry<?, ?> e : cases.entrySet()) {
            String key = String.valueOf(e.getKey());
            if ("default".equals(key) || key.equals(chosen)) continue;
            others.addAll(recordDeclaredFields(e.getValue(), null));
        }
        if (chosen != null && dflt != null) others.addAll(recordDeclaredFields(dflt, null));
        Set<String> outside = new java.util.HashSet<>();
        for (Object[] e : enclosing) {
            List<Object> raws = new ArrayList<>();
            for (Object f : (List<?>) e[0]) {
                Field other = (Field) f;
                if (other != e[1] && other.getRaw() != null) raws.add(other.getRaw());
            }
            outside.addAll(recordDeclaredFields(raws, null));
        }
        for (String n : others) {
            if (!mine.contains(n) && !outside.contains(n) && data.containsKey(n)) {
                throw new SchemaException.EncodeException("Field " + matchLabel(match)
                        + ": the input gives '" + n + "', which only another case declares, "
                        + "against the recorded case (PS-573)");
            }
        }
    }

    /** The reference's {@code record_declared_fields}, over a raw field list. */
    static List<String> recordDeclaredFields(Object fields, Object skip) {
        List<String> found = new ArrayList<>();
        walkDeclared(fields, skip, found);
        return found;
    }

    private static void walkDeclared(Object items, Object skip, List<String> found) {
        if (!(items instanceof List<?> list)) return;
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> m) || item == skip) continue;
            if (m.containsKey("type")) {
                if (m.get("name") instanceof String n && !n.isEmpty() && !found.contains(n)) found.add(n);
                walkDeclared(m.get("trailer"), skip, found);
                continue;
            }
            Object spec = null;
            for (String k : List.of("match", "tlv", "flagged", "byte_group", "records")) {
                if (m.containsKey(k)) { spec = m.get(k); break; }
            }
            if (spec instanceof List<?> parts) {
                for (Object part : parts) walkDeclared(part instanceof List<?> ? part : List.of(part), skip, found);
            } else if (spec instanceof Map<?, ?> sm) {
                walkDeclared(sm.get("fields"), skip, found);
                if (sm.get("cases") instanceof Map<?, ?> cs) {
                    for (Object body : cs.values()) walkDeclared(body, skip, found);
                }
                if (sm.get("default") instanceof List<?> def) walkDeclared(def, skip, found);
                if (sm.get("groups") instanceof List<?> groups) {
                    for (Object g : groups) {
                        if (g instanceof Map<?, ?> gm) walkDeclared(gm.get("fields"), skip, found);
                    }
                }
            }
        }
    }

    /**
     * Rebuild a {@code match} construct's bytes from decoded output.
     *
     * <p>Two sources of the discriminator, encoded differently. An inline match with
     * {@code length: N} read those bytes itself, so they are written back. A match on
     * {@code field: $var} read nothing, so writing the discriminator here would duplicate
     * the earlier field that produced it.
     */
    private byte[] encodeMatch(Field match, Map<String, Object> data, Object holder,
                               List<Field> siblings) {
        List<Field.Case> cases = match.getCases();
        if (cases == null) cases = List.of();

        Object discriminator = null;
        String name = match.getName();
        if (name != null && data.containsKey(name)) {
            discriminator = data.get(name);
        } else if (match.getOn() != null && !match.getOn().isEmpty()) {
            String varName = match.getOn().startsWith("$")
                    ? match.getOn().substring(1) : match.getOn();
            if (data.containsKey(varName)) {
                // The output holds what the field reported; the match compared what a
                // reference to it binds (PS-523), the number under a text label.
                discriminator = boundFromReported(fieldNamed(varName), data.get(varName));
            } else {
                // The variable's name is often not the field's: rbs30x has
                // `name: event_type` with `var: evt`, and matches on `$evt`. Decoded
                // output is keyed by the field name, so encoding has to get from one to
                // the other - and undo the field's lookup, since the output holds a label.
                Field source = fieldDeclaringVar(varName);
                if (source != null && source.getName() != null
                        && data.containsKey(source.getName())) {
                    discriminator = boundFromReported(source, data.get(source.getName()));
                }
            }
        }

        if (discriminator == null && match.getVar() != null && data.get(match.getVar()) != null) {
            // An inline discriminator bound to a variable the rest of the input fixes -
            // dnt's presence mask, which the flagged block after it reads.
            discriminator = data.get(match.getVar());
        }
        byte[] peek = peeked;
        if (discriminator == null && match.getLength() > 0 && peek != null
                && peek.length >= match.getLength()) {
            // CR-2026-136: a bit range before the match peeked these bytes and has filled
            // them, so they are the discriminator the decoder will read (r718x's command
            // and device bytes, dnt's first command).
            long v = 0;
            for (int i = 0; i < match.getLength(); i++) {
                int b = peek[little ? match.getLength() - 1 - i : i] & 0xFF;
                v = (v << 8) | b;
            }
            discriminator = v;
        }
        // PS-573: where the input gives no discriminator, the decode record's is written and
        // its case taken - before any derivation, which PS-574 makes the fallback.
        boolean given = discriminator != null;
        String onName = match.getOn() == null ? null
                : match.getOn().startsWith("$") ? match.getOn().substring(1) : match.getOn();
        if (onName != null && recordSupplied.contains(onName)) given = false;
        Map<?, ?> record = null;
        if (rec != null && rec.lookup(rec.matches, match.getRaw()) instanceof Map<?, ?> m) record = m;
        Object recordedKey = null;              // a Field.Case, or "default"
        if (!given && record != null) {
            if (match.isMatchRemaining()) recordedKey = recordedCase(match, record.get("case"));
            else if (discriminator == null) discriminator = record.get("value");
        }
        if (match.getLength() > 0) checkWrittenDiscriminator(discriminator, name);  // PS-557
        if (match.getLength() > 0 && discriminator instanceof Number number
                && !(discriminator instanceof Boolean)) {
            // PS-168, before a case is looked for, as the spec prototype checks it.
            long[] db = integerBounds(match.getLength() * 8, false);
            String fitName = name != null && !name.isEmpty() ? name
                    : match.getVar() != null && !match.getVar().isEmpty() ? match.getVar() : "match";
            checkFits((long) Math.rint(number.doubleValue()), db[0], db[1], fitName, discriminator);
        }

        Field.Case matched = null;
        List<Field> fallbackFields = null;
        if (recordedKey instanceof Field.Case c) {
            matched = c;
        } else if (recordedKey != null) {
            for (Field.Case c : cases) {
                if (c.isDefault()) matched = c;
            }
            if (matched == null) {
                fallbackFields = match.getMatchDefault() instanceof List<?> declared
                        ? (List<Field>) declared : List.of();
            }
        }
        if (discriminator != null && matched == null && fallbackFields == null) {
            for (Field.Case c : cases) {
                if (c.isDefault()) continue;
                if (caseMatches(discriminator, c.getCaseValue())) {
                    matched = c;
                    break;
                }
            }
        }
        // A known discriminator that matched no case takes the default, and takes it
        // ahead of the claimable-name heuristic: the schema said what an unmatched value
        // means, so guessing a case from the names present would contradict it. The
        // heuristic stays for an inline match whose discriminator is not in the data.
        if (matched == null && fallbackFields == null && discriminator != null) {
            for (Field.Case c : cases) {
                if (c.isDefault()) {
                    matched = c;
                    break;
                }
            }
            if (matched == null && match.getMatchDefault() instanceof List<?> declared) {
                // The `default:` key beside `cases`, which nothing read here: a schema
                // declaring a fallback wrote the discriminator and nothing else, so
                // match-default-fields.yaml re-encoded `097f` as `09` (CR-2026-027).
                // Already Fields; the parser converts the list once.
                fallbackFields = (List<Field>) declared;
            }
        }
        if (record != null && !given) {                                     // PS-573
            checkRecordedCase(match, matched != null && !matched.isDefault()
                    ? String.valueOf(matched.getCaseValue()) : null, data);
        }
        if (matched == null && fallbackFields == null && discriminator == null) {
            // An inline match with no name reports nothing of itself, and a `remaining`
            // match has none to report, so the case is recovered from which of its fields
            // the data carries, scored through the constructs in it (_best_case). A given
            // discriminator matching no case takes the default or is rejected (PS-565):
            // the fields given cannot overrule it.
            BestCase best = bestCase(match, data);
            if (best != null) matched = best.c();
        }
        if (matched == null && fallbackFields == null && match.isMatchRemaining()
                && defaultScore(match, data) == 0) {
            // No case's fields given, nor the default's: the case matching 0 bytes is the
            // one writing nothing selects (pm-io-5-sm's `0..1: []`).
            for (Field.Case c : cases) {
                if (!c.isDefault() && c.getFields() != null && caseMatches(0L, c.getCaseValue())) {
                    matched = c;
                    break;
                }
            }
        }
        String label = name != null && !name.isEmpty() ? name
                : match.getVar() != null && !match.getVar().isEmpty() ? match.getVar() : "match";
        if (matched == null && fallbackFields == null && discriminator == null
                && match.getLength() > 0) {
            // CR-2026-151 (PS-551): a nested match reading this discriminator,
            // with exactly one case the input fills, fixes it.
            Object[] nested = nestedDiscriminator(match, data, label);
            if (nested != null) {
                discriminator = nested[0];
                if (nested[1] instanceof Field.Case c) matched = c;
                else fallbackFields = (List<Field>) nested[2];
                if (match.getVar() != null && !match.getVar().isEmpty()) {
                    Map<String, Object> withVar = new LinkedHashMap<>(data);
                    withVar.put(match.getVar(), discriminator);
                    data = withVar;
                }
            }
        }
        if (matched == null && fallbackFields == null && discriminator == null
                && match.getMatchDefault() instanceof List<?> declared) {
            // The `default:` beside `cases`, taken as the reference takes it where nothing
            // in the input belongs to a case; its discriminator is then missing, below.
            fallbackFields = (List<Field>) declared;
        }
        if (matched == null && fallbackFields == null) {
            for (Field.Case c : cases) {
                if (c.isDefault()) {
                    matched = c;
                    break;
                }
            }
        }
        if (matched == null && fallbackFields == null) {
            if (match.getMatchDefault() instanceof List<?> declared) {
                fallbackFields = (List<Field>) declared;
            } else if (match.getLength() > 0 && discriminator == null) {
                // PS-551, PS-442: an inline discriminator the input neither gives nor fixes
                // through a case's fields, nor a bit range before it peeked. This returned
                // no bytes where the match was nameless or its default `skip`.
                throw MissingFieldException.undetermined(label);   // named by the discriminator (CR-2026-151)
            } else if ("skip".equals(match.getMatchDefault())) {
                // The discriminator, if the match reads one, and nothing for the case.
                fallbackFields = List.of();
            } else if (discriminator != null) {
                // PS-442: no case for the discriminator given, and no default.
                throw new SchemaException.EncodeException(
                        "No matching case for value " + discriminator);
            } else {
                // PS-442, PS-551: no discriminator given or derivable, and no case whose
                // fields the input gives. This wrote nothing and reported nothing.
                String on = match.getOn();
                if (on != null && on.startsWith("$")) on = on.substring(1);
                throw MissingFieldException.undetermined(!"match".equals(label) ? label
                        : (on != null && !on.isEmpty() ? on : "match"));
            }
        }
        if (fallbackFields != null) {
            ByteBuf fallbackOut = new ByteBuf();
            if (match.getLength() > 0) {
                Long value = asLong(discriminator);
                // A `default` stands for every unlisted value, so it names none to write;
                // a zero was written here (PS-551).
                if (value == null) throw MissingFieldException.undetermined(label);
                long[] db = integerBounds(match.getLength() * 8, false);
                checkFits(value, db[0], db[1], label, discriminator);              // PS-168
                fallbackOut.write(writeInt(value, match.getLength(), false));
            }
            byte[] body = encodeFieldList(fallbackFields, data);
            registerRemaining(match, body.length, "default");                  // PS-564
            Integer tail = carriedTail.remove(body);
            int prefix = fallbackOut.size;
            fallbackOut.write(body);
            return carrying(prefixed(prefix, body, fallbackOut.toArray()), tail == null ? 0 : tail);
        }

        ByteBuf out = new ByteBuf();
        if (match.getLength() > 0) {
            Long value = asLong(discriminator);
            if (value == null) {
                if (matched.isDefault()) {
                    throw MissingFieldException.undetermined(label);
                }
                Object key = matched.getCaseValue();
                Long fromKey = key == null ? null : parseIntAny(String.valueOf(key));
                if (fromKey == null) {
                    throw new SchemaException.EncodeException("match case " + key
                            + " names no single discriminator value");
                }
                value = fromKey;
            }
            long[] db = integerBounds(match.getLength() * 8, false);
            checkFits(value, db[0], db[1], label, discriminator == null ? value : discriminator);
            out.write(writeInt(value, match.getLength(), false));
        }
        byte[] body = encodeFieldList(matched.getFields(), data);
        registerRemaining(match, body.length,
                matched.isDefault() ? "default" : String.valueOf(matched.getCaseValue()));
        Integer tail = carriedTail.remove(body);
        int prefix = out.size;
        out.write(body);
        // The body's unconsumed tail stays pending in the enclosing list (CR-2026-136).
        return carrying(prefixed(prefix, body, out.toArray()), tail == null ? 0 : tail);
    }

    // --- PS-564: the bytes a `remaining` match writes select its case -------------------

    /** A `remaining` match's chosen case and the bytes from it to the end so far. */
    private static final class TailCheck {
        final Field match;
        final String chosen;
        int tail;

        TailCheck(Field match, int tail, String chosen) {
            this.match = match;
            this.tail = tail;
            this.chosen = chosen;
        }
    }

    private final List<TailCheck> tailChecks = new ArrayList<>();
    /** Inside a tlv or repeat, where the bytes after a match are not in one field list. */
    private int tailDepth = 0;

    private void registerRemaining(Field match, int bodyLength, String chosen) {
        if (match.isMatchRemaining() && tailDepth == 0) {
            tailChecks.add(new TailCheck(match, bodyLength, chosen));
        }
    }

    /**
     * One field list's share of the bytes after a `remaining` match (the reference's
     * TailLevel): once an item is written, the bytes it added go to the tail of every check
     * an earlier item of the same list registered.
     */
    private final class TailLevel {
        private final FrameBuffer out;
        private final List<TailCheck> checks = new ArrayList<>();
        private int size = -1;
        private int mark = 0;

        TailLevel(FrameBuffer out) {
            this.out = out;
        }

        void start() {
            close();
            size = out.size();
            mark = tailChecks.size();
        }

        void close() {
            if (size < 0) return;
            int grown = out.size() - size;
            for (TailCheck c : checks) c.tail += grown;
            checks.addAll(tailChecks.subList(mark, tailChecks.size()));
            size = -1;
        }
    }

    /** The case a decoder selects for a `remaining` match with `count` bytes left. */
    private static String remainingSelection(Field match, int count) {
        List<Field.Case> cases = match.getCases() == null ? List.of() : match.getCases();
        boolean hasDefault = match.getMatchDefault() instanceof List<?>
                || "skip".equals(match.getMatchDefault());
        for (Field.Case c : cases) {
            if (c.isDefault()) {
                hasDefault = true;
                continue;
            }
            if (caseMatches((long) count, c.getCaseValue())) return String.valueOf(c.getCaseValue());
        }
        return hasDefault ? "default" : null;
    }

    static String remainingMismatchMessage(Field match, int count, String selected, String chosen) {
        String name = match.getName();
        String label = name != null && !name.isEmpty() ? "'" + name + "'" : "an unnamed match";
        return "Field " + label + ": the " + count + " byte(s) from it to the end of the payload select "
                + describeCase(selected) + ", not " + describeCase(chosen)
                + ", the case its input gives (PS-564)";
    }

    private static String describeCase(String key) {
        if (key == null) return "no case";
        if ("default".equals(key)) return "its default";
        return "case " + key;
    }

    /**
     * CR-2026-151 (PS-551), applied on spec main for 0.5.5: the value a nested
     * match fixes for this match's discriminator, as {value, the outer case or null, the
     * `default` fields}, or null where no nested match reading it has a case the input
     * fills. The input fixes it where it fills exactly one such case, whose key is a single
     * value selecting, in this match and in every match between that reads it, the branch
     * holding the nested match; otherwise it is rejected, naming the discriminator. Mirrors
     * the reference's {@code _nested_discriminator}.
     */
    @SuppressWarnings("unchecked")
    private static Object[] nestedDiscriminator(Field match, Map<String, Object> data, String label) {
        Set<String> names = new HashSet<>();
        if (match.getVar() != null && !match.getVar().isEmpty()) names.add(match.getVar());
        if (match.getName() != null && !match.getName().isEmpty()) names.add(match.getName());
        if (names.isEmpty()) return null;
        // A step is {the match, the branch taken: a Case, or null for the default beside}.
        List<Object[]> filled = new ArrayList<>();   // {path, filled case}
        List<Object[]> start = new ArrayList<>();
        java.util.function.BiConsumer<List<Field>, List<Object[]>>[] walk = new java.util.function.BiConsumer[1];
        walk[0] = (fields, path) -> {
            if (fields == null) return;
            for (Field f : fields) {
                Field inner = f.getMatchInline();
                if (inner == null) continue;
                String on = inner.getOn() == null ? "" : inner.getOn().replaceFirst("^\\$+", "");
                boolean reads = !on.isEmpty() && names.contains(on);
                Set<String> given = new HashSet<>();
                if (reads && inner.getCases() != null) {
                    for (Field.Case c : inner.getCases()) {
                        if (c.isDefault()) continue;
                        for (String n : namesOf(c.getFields())) if (data.containsKey(n)) given.add(n);
                    }
                }
                if (inner.getCases() != null) {
                    for (Field.Case c : inner.getCases()) {
                        List<Object[]> next = path;
                        if (reads) {
                            if (!c.isDefault() && caseFilled(namesOf(c.getFields()), given, data)) {
                                filled.add(new Object[] {path, c});
                            }
                            next = new ArrayList<>(path);
                            next.add(new Object[] {inner, c});
                        }
                        walk[0].accept(c.getFields(), next);
                    }
                }
                if (inner.getMatchDefault() instanceof List<?> fb) {
                    List<Object[]> next = path;
                    if (reads) {
                        next = new ArrayList<>(path);
                        next.add(new Object[] {inner, null});
                    }
                    walk[0].accept((List<Field>) fb, next);
                }
            }
        };
        if (match.getCases() != null) {
            for (Field.Case c : match.getCases()) {
                List<Object[]> path = new ArrayList<>();
                path.add(new Object[] {match, c});
                walk[0].accept(c.getFields(), path);
            }
        }
        if (match.getMatchDefault() instanceof List<?> fb) {
            List<Object[]> path = new ArrayList<>();
            path.add(new Object[] {match, null});
            walk[0].accept((List<Field>) fb, path);
        }
        if (filled.isEmpty()) return null;
        if (filled.size() != 1) throw MissingFieldException.undetermined(label);
        List<Object[]> path = (List<Object[]>) filled.get(0)[0];
        Field.Case filledCase = (Field.Case) filled.get(0)[1];
        Long value = exactCaseValue(filledCase.getCaseValue());
        if (value == null) throw MissingFieldException.undetermined(label);
        for (Object[] step : path) {
            Field enclosing = (Field) step[0];
            Field.Case selected = null;
            if (enclosing.getCases() != null) {
                for (Field.Case c : enclosing.getCases()) {
                    if (!c.isDefault() && caseMatches(value, c.getCaseValue())) {
                        selected = c;
                        break;
                    }
                }
                if (selected == null && step[1] != null && ((Field.Case) step[1]).isDefault()) {
                    selected = (Field.Case) step[1];      // an in-cases default
                }
            }
            if (selected != step[1]) throw MissingFieldException.undetermined(label);
        }
        Object outer = path.get(0)[1];
        if (outer instanceof Field.Case c) return new Object[] {value, c, null};
        return new Object[] {value, null, match.getMatchDefault()};
    }

    private static List<String> namesOf(List<Field> fields) {
        List<String> out = new ArrayList<>();
        if (fields == null) return out;
        for (Field f : fields) if (payloadName(f) != null) out.add(f.getName());
        return out;
    }

    /**
     * A case is filled where the input gives every one of its fields and nothing any other
     * case of its match declares beyond them (Netvox r718n3's case 3 holds the fields of
     * cases 1 and 2 together).
     */
    private static boolean caseFilled(List<String> own, Set<String> given, Map<String, Object> data) {
        if (own.isEmpty()) return false;
        for (String n : own) if (!data.containsKey(n)) return false;
        return own.containsAll(given);
    }

    private static boolean caseMatches(Object discriminator, Object caseValue) {
        if (caseValue == null) return false;
        Double number = discriminator instanceof Boolean ? null : asDouble(discriminator);
        if (number != null && number != Math.rint(number) && !number.isInfinite()) {
            // A fraction selects no case it would round to: 2.5 chose case 2 here, where
            // every decoder reads it as matching no single key (Schema.decodeMatch).
            return Schema.fractionInRange(number, caseValue);
        }
        Long value = asLong(discriminator);
        if (value == null) {
            return String.valueOf(discriminator).equals(String.valueOf(caseValue));
        }
        if (caseValue instanceof Number n) {
            return value == n.longValue();
        }
        if (caseValue instanceof String text && Schema.parseListCaseKey(text) != null) {
            return Schema.parseListCaseKey(text).contains(value);
        }
        if (caseValue instanceof String spelled && spelled.contains("..")) {
            // An inclusive range (PS-398), as the decoder reads it; it fell through to
            // parseIntAny and matched nothing.
            return Schema.fractionInRange(value, caseValue);
        }
        if (caseValue instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Number n && value == n.longValue()) return true;
            }
            return false;
        }
        if (caseValue instanceof Map<?, ?> range) {
            Long min = asLong(range.get("min"));
            Long max = asLong(range.get("max"));
            return (min == null || value >= min) && (max == null || value <= max);
        }
        Long parsed = parseIntAny(String.valueOf(caseValue));
        return parsed != null && value.longValue() == parsed.longValue();
    }

    /**
     * How well a candidate TLV case explains the data: how many of its fields are present,
     * and whether it could have produced their values at all.
     *
     * <p>Two cases can define the same field name under different tags - am308 has
     * {@code tvoc} under both {@code [8, 125]} ({@code div: 100}) and {@code [8, 230]}
     * (raw). Only one of them wrote those bytes, and the arithmetic says which: 43.69 came
     * from 4369 through {@code div: 100} exactly, while the raw case would need it rounded.
     * A candidate that cannot reproduce the value it claims is ranked behind one that can.
     */
    /**
     * Flatten a tlv case to the fields whose values are looked up in the case's own data
     * map, descending into the constructs that carry no name of their own.
     *
     * <p>A byte_group or flagged field is nameless: its names sit in its group's fields,
     * and encodeByteGroup and encodeFlagged both read them straight out of the same flat
     * map. Collecting only the top level therefore found nothing to claim for such a case,
     * so it never became a candidate and the channel encoded to no bytes <em>and no
     * error</em>. {@code hbi/mla20}'s case {@code 0x20} is two of these, and
     * {@code _language-conformance/tlv-nameless-case.yaml} pins it.
     *
     * <p>Deliberately not descended into: an object's or repeat's {@code fields}, whose
     * values live in a nested map under the field's own name rather than in this map, so
     * claiming their members would claim names this map does not have — the field's own
     * name is claimed instead, which is what the nested objects in the milesight schemas
     * rely on; and a nested match or tlv, where which branch supplies a name depends on
     * the data, so claiming every branch's names would over-claim.
     */
    private static void collectClaimable(List<Field> fields, List<Field> out) {
        if (fields == null) return;
        for (Field f : fields) {
            if (f.getByteGroup() != null && !f.getByteGroup().isEmpty()) {
                collectClaimable(f.getByteGroup(), out);
                continue;
            }
            if (f.getFlagged() != null && f.getFlagged().getGroups() != null) {
                for (Field.FlaggedGroup group : f.getFlagged().getGroups()) {
                    collectClaimable(group.getFields(), out);
                }
                continue;
            }
            if (payloadName(f) == null) continue;
            out.add(f);
        }
    }

    private static List<Field> claimableFields(List<Field> fields) {
        List<Field> out = new ArrayList<>();
        collectClaimable(fields, out);
        return out;
    }

    /**
     * The reference's {@code _case_takes} (CR-2026-148, PS-527 amended): whether a tlv
     * case's field can take a value, and whether it reproduces it exactly, as
     * {fits, exact}. Fits: the value with the field's arithmetic undone (and, for an
     * integer type or bit range, rounded per PS-552) lies within the type's range
     * (PS-168); a float takes any finite value no larger than its largest. Exact: the
     * arithmetic re-applied to the raw value written, through the type's storage, gives
     * the value under binary64 equality. No tolerance anywhere; the test this replaces
     * asked for an integer within an absolute 1e-9, which refused every float field and
     * 0xfffffe through {@code div: 100}.
     */
    static boolean[] caseTakes(Field f, Object value) {
        if (Cr181.isByteString(f.getType())) {                      // CR-2026-181 (PS-527)
            boolean took = Cr181.takes(f, value);
            return new boolean[] {took, took};
        }
        if (f.getType() == FieldType.REPEAT && value instanceof List<?> list) {
            return Cr181.repeatTakes(f, list);
        }
        Object reversed = reverseLookup(value, f.getLookup());
        if (reversed instanceof Boolean || !(reversed instanceof Number number)) {
            return new boolean[] {true, true};
        }
        double given = number.doubleValue();
        double raw;
        try {
            raw = reverseCanonicalModifiers(reverseTransformStages(given, f.getTransform()), f);
        } catch (RuntimeException e) {
            return new boolean[] {false, false};
        }
        if (Double.isNaN(raw) || Double.isInfinite(raw)) return new boolean[] {false, false};
        FieldType t = f.getType();
        double stored;
        if (t == FieldType.BITS) {
            stored = Math.rint(raw);
            if (!fitsBits(stored, f.getBits(), f.isSignedBits())) return new boolean[] {false, false};
        } else if (t != null && t.isInteger()) {
            stored = Math.rint(raw);
            if (!fitsBits(stored, t.defaultLength() * 8, t.isSigned())) {
                return new boolean[] {false, false};
            }
        } else if (t == FieldType.BF16) {
            try {
                stored = Bf16.decode(Bf16.encode(raw, f.getName()));   // PS-559's rounding
            } catch (RuntimeException e) {
                return new boolean[] {false, false};
            }
        } else if (t != null && floatMax(t) != null) {
            if (Math.abs(raw) > floatMax(t)) return new boolean[] {false, false};
            stored = switch (t) {
                case F64, FLOAT64 -> raw;
                case F16, FLOAT16 -> f16Round(raw);
                default -> (double) (float) raw;
            };
        } else if (t == FieldType.UDEC || t == FieldType.SDEC) {
            double n = Math.rint(raw * 10);                                      // PS-556
            double low = t == FieldType.SDEC ? -80 : 0, high = t == FieldType.SDEC ? 79 : 159;
            if (n < low || n > high) return new boolean[] {false, false};
            double whole = Math.floor(n / 10);
            stored = whole + (n - whole * 10) * 0.1;
        } else if (t == FieldType.UFLT16 || t == FieldType.SFLT16 || t == FieldType.SFLT24) {
            Double v;
            try {
                v = Wave4.decodeMinifloat(t, Wave4.encodeMinifloat(t, raw));
            } catch (RuntimeException e) {
                return new boolean[] {false, false};
            }
            if (v == null) return new boolean[] {false, false};
            stored = v;
        } else {
            return new boolean[] {true, true};
        }
        return new boolean[] {true, Schema.applyArithmetic(stored, f) == given};
    }

    /** The largest finite magnitude of a binary float type, or null (CR-2026-148). */
    private static Double floatMax(FieldType t) {
        return switch (t) {
            case F16, FLOAT16 -> 65504.0;
            case F32, FLOAT32, F32LE16, F32BE16LE -> (double) Float.MAX_VALUE;
            case F64, FLOAT64 -> Double.POSITIVE_INFINITY;
            default -> null;
        };
    }

    /** Whether an integer value lies within {@code bits} bits, signed or not. */
    private static boolean fitsBits(double v, int bits, boolean signed) {
        if (signed) return v >= -Math.scalb(1.0, bits - 1) && v < Math.scalb(1.0, bits - 1);
        return v >= 0 && v < Math.scalb(1.0, bits);
    }

    /** A value rounded to the nearest IEEE half, a tie to even, straight from binary64. */
    static double f16Round(double x) {
        if (x == 0 || Double.isInfinite(x) || Double.isNaN(x)) return x;
        double a = Math.abs(x);
        int e = Math.max(Math.getExponent(a), -14);
        double q = Math.scalb(1.0, e - 10);
        double r = Math.rint(a / q) * q;
        return x < 0 ? -r : r;
    }

    /** A claimable field of a tlv case, and whether the case may be written without it. */
    private record Claim(Field field, boolean optional) {}

    /**
     * The reference's {@code _case_claims}: claimable fields, a flagged group's (whose
     * mask says which are present) and an {@code optional: true} field marked optional.
     */
    private static void collectClaims(List<Field> fields, boolean optional, List<Claim> out) {
        if (fields == null) return;
        Set<Field> covered = coveredPeeks(fields);
        for (Field f : fields) {
            if (f.getByteGroup() != null && !f.getByteGroup().isEmpty()) {
                collectClaims(f.getByteGroup(), optional, out);
                continue;
            }
            if (f.getFlagged() != null && f.getFlagged().getGroups() != null) {
                for (Field.FlaggedGroup group : f.getFlagged().getGroups()) {
                    collectClaims(group.getFields(), true, out);
                }
                continue;
            }
            if (f.getMatchInline() != null) {
                matchClaims(f.getMatchInline(), out);
                continue;
            }
            if (claimName(f) != null && (isLiteral(f) || isComputed(f) || f.isEcho())) {
                // A literal or computed field writes no bytes, but only this case reports
                // it, so it claims its name, never required: a literal where the input gives
                // its value, a computed field so the case is chosen and what it is computed
                // from is asked for (PS-434). Neither claimed anything, so an entry
                // reporting only these was never written and the encode returned no bytes.
                out.add(new Claim(f, true));
                continue;
            }
            if (payloadName(f) == null) continue;
            // PS-551 (CR-2026-141): a peek the case's next field writes over is derived
            // where the input omits it, so the case does not need it given.
            out.add(new Claim(f, optional || f.isOptional() || covered.contains(f)));
        }
    }

    /**
     * The reference's {@code _match_claims}: a match claims its {@code name} and every
     * branch's fields, each optional, since which branch supplies a name depends on the
     * data; each name once. Not descending into it, a tlv case whose only content is a
     * nameless match claimed nothing and was never written - the encode returned an empty
     * payload with no error - and an object holding one refused every value.
     */
    @SuppressWarnings("unchecked")
    private static void matchClaims(Field match, List<Claim> out) {
        Set<String> seen = new HashSet<>();
        String name = payloadName(match);
        if (name != null) {
            Field claim = new Field();
            claim.setName(name);
            claim.setType(switch (match.getLength()) {
                case 2 -> FieldType.U16;
                case 3 -> FieldType.U24;
                case 4 -> FieldType.U32;
                case 8 -> FieldType.U64;
                default -> FieldType.U8;
            });
            out.add(new Claim(claim, true));
            seen.add(name);
        }
        List<List<Field>> bodies = new ArrayList<>();
        if (match.getCases() != null) {
            for (Field.Case c : match.getCases()) bodies.add(c.getFields());
        }
        if (match.getMatchDefault() instanceof List<?> fallback) bodies.add((List<Field>) fallback);
        for (List<Field> body : bodies) {
            List<Claim> inner = new ArrayList<>();
            collectClaims(body, true, inner);
            for (Claim c : inner) {
                // A literal is seen by its value as well: branches report one name as
                // different literals (aquascope's `pc`), each a claim of its own.
                String key = isLiteral(c.field())
                        ? c.field().getName() + "\u0000" + c.field().getValue()
                        : c.field().getName();
                if (seen.add(key)) out.add(new Claim(c.field(), true));
            }
        }
    }

    /** Whether a type's encoded value carries a fraction (the reference's FRACTIONAL_TYPES). */
    private static boolean isFractionalType(FieldType t) {
        if (t == null) return false;
        return t.isFloat() || switch (t) {
            case UDEC, SDEC, F32LE16, F32BE16LE, UFLT16, SFLT16, SFLT24 -> true;
            default -> false;
        };
    }

    /**
     * The reference's {@code reverses_to_integer}, a proto rule the specification does not
     * state: round half to even, as PS-552 rounds what is written, and accept the value
     * within 1e-9 x max(1, |n|) of that integer n. An absolute 1e-9 refused 0xfffffe through
     * {@code div: 100}, which reverses to 16777214.000000002.
     */
    static boolean reversesToInteger(double raw) {
        double n = Math.rint(raw);
        return Math.abs(raw - n) <= 1e-9 * Math.max(1.0, Math.abs(n));
    }

    /**
     * How many fields a case's claims count for when ranking cases (PS-527 as CR-2026-129
     * states it): an object counts its members, so a merged {adr, dr} case outranks a
     * {dr} one, and anything else counts one.
     */
    private static int claimWidth(List<Field> fields) {
        int n = 0;
        for (Field f : fields) {
            if (f.getType() == FieldType.OBJECT) {
                List<Claim> members = new ArrayList<>();
                collectClaims(f.getFields(), false, members);
                n += Math.max(1, members.size());
            } else {
                n++;
            }
        }
        return n;
    }

    private record TlvEntry(String key, List<Field> fields, List<Field> required,
                            List<Field> optional, int rank) {}

    private record Written(String key, List<Field> fields, Map<String, Object> data,
                           int position, int seq) {}

    /**
     * Rebuild a TLV payload from decoded output.
     *
     * <p>PS-525 to PS-527 (CR-2026-110, -125, -128): each value - a scalar, each element of
     * an array PS-157 collected, a {@code last} value, a merged object's members - goes
     * under the first case whose declared fields it supplies in full; nothing is
     * zero-filled or dropped, and what no case takes is an error. A case requiring more
     * fields is tried first, then declaration order, so em400-mud's [131, 103]
     * (temperature, temperature_alarm) is not stranded by [3, 103] (temperature).
     */
    private byte[] encodeTLV(Field tlv, Map<String, Object> data) {
        // PS-564 is not checked inside a tlv or repeat (tail_unchecked).
        tailDepth++;
        try {
            return encodeTLVBody(tlv, data);
        } finally {
            tailDepth--;
        }
    }

    private byte[] encodeTLVBody(Field tlv, Map<String, Object> data) {
        // `merge: false` reports its entries as a list under `channels`, which the claiming
        // below cannot see, so this wrote nothing and reported success. Encoding the list
        // is not defined yet; refuse it by name, as the reference does.
        if (Boolean.FALSE.equals(tlv.getMerge())) {
            throw new SchemaException.EncodeException("a tlv with merge: false reports its entries under 'channels', and encoding them is not supported");
        }
        Map<String, List<Field>> cases = tlv.getTlvCases();
        if (cases == null || cases.isEmpty()) return EMPTY;
        int lengthSize = Math.max(0, tlv.getLengthSize());
        List<String> order = new ArrayList<>(data.keySet());
        OnRepeat.Modes modes = tlv.getRepeatModes();

        List<TlvEntry> entries = new ArrayList<>();
        Map<String, Set<String>> declaredMembers = new LinkedHashMap<>();
        for (Map.Entry<String, List<Field>> e : cases.entrySet()) {
            if ("default".equals(e.getKey()) || e.getValue() == null) continue;
            List<Claim> claims = new ArrayList<>();
            collectClaims(e.getValue(), false, claims);
            List<Field> required = new ArrayList<>(), optional = new ArrayList<>();
            for (Claim c : claims) {
                (c.optional() ? optional : required).add(c.field());
                String name = claimName(c.field());
                if ("merge".equals(modes.mode(name)) && c.field().getFields() != null) {
                    for (Field m : c.field().getFields()) {
                        if (m.getName() != null && !m.getName().isEmpty()) {
                            declaredMembers.computeIfAbsent(name, k -> new LinkedHashSet<>()).add(m.getName());
                        }
                    }
                }
            }
            entries.add(new TlvEntry(e.getKey(), e.getValue(), required, optional, entries.size()));
        }
        entries.sort((x, y) -> Integer.compare(claimWidth(y.required()), claimWidth(x.required())));

        // A supplied member of a merged object that no case declares has nowhere to go.
        for (Map.Entry<String, Set<String>> e : declaredMembers.entrySet()) {
            if (!(data.get(e.getKey()) instanceof Map<?, ?> object)) continue;
            List<String> stray = new ArrayList<>();
            for (Object k : object.keySet()) {
                if (!e.getValue().contains(String.valueOf(k))) stray.add("'" + k + "'");
            }
            if (!stray.isEmpty()) {
                java.util.Collections.sort(stray);
                throw new SchemaException.EncodeException("tlv: the merged object '" + e.getKey()
                        + "' is given [" + String.join(", ", stray) + "], which no case declares (PS-527)");
            }
        }

        Map<String, java.util.ArrayDeque<Object>> queues = new LinkedHashMap<>();
        for (TlvEntry e : entries) {
            List<Field> all = new ArrayList<>(e.required());
            all.addAll(e.optional());
            for (Field f : all) {
                String n = claimName(f);
                if (!data.containsKey(n) || queues.containsKey(n) || "merge".equals(modes.mode(n))) continue;
                Object v = data.get(n);
                java.util.ArrayDeque<Object> q = new java.util.ArrayDeque<>();
                if (v instanceof List<?> list && !OnRepeat.reportsList(f) && "collect".equals(modes.mode(n))) {
                    q.addAll(list);
                } else {
                    q.add(v);
                }
                queues.put(n, q);
            }
        }
        Map<String, Set<String>> pending = new LinkedHashMap<>();
        for (String n : declaredMembers.keySet()) {
            if (data.get(n) instanceof Map<?, ?> object) {
                Set<String> keys = new LinkedHashSet<>();
                for (Object k : object.keySet()) keys.add(String.valueOf(k));
                pending.put(n, keys);
            }
        }

        List<Written> written = new ArrayList<>();
        // CR-2026-148 (PS-527 amended): a first sweep places only values a case reproduces
        // exactly, a second the rest; the ranking applies within each.
        for (boolean exactOnly : new boolean[] {true, false}) {
            boolean progress = true;
            while (progress) {
                progress = false;
                for (TlvEntry e : entries) {
                    while (true) {
                        Map<String, Object> values = new LinkedHashMap<>();
                        int taken = 0;
                        boolean complete = true;
                        for (Field f : e.required()) {
                            String n = claimName(f);
                            if ("merge".equals(modes.mode(n))) {
                                if (!pending.containsKey(n) || !(data.get(n) instanceof Map<?, ?> object)) {
                                    complete = false;
                                    break;
                                }
                                Map<String, Object> sub = new LinkedHashMap<>();
                                boolean fresh = false;
                                for (Field m : f.getFields() == null ? List.<Field>of() : f.getFields()) {
                                    if (m.getName() == null || m.getName().isEmpty()) continue;
                                    if (!object.containsKey(m.getName())) {
                                        complete = false;
                                        break;
                                    }
                                    sub.put(m.getName(), object.get(m.getName()));
                                    fresh |= pending.get(n).contains(m.getName());
                                }
                                if (!complete) break;
                                values.put(n, sub);
                                if (fresh) taken++;
                                continue;
                            }
                            java.util.ArrayDeque<Object> q = queues.get(n);
                            if (q == null || q.isEmpty() || !fits(f, q.peekFirst(), modes, exactOnly)) {
                                complete = false;
                                break;
                            }
                            values.put(n, q.peekFirst());
                            taken++;
                        }
                        if (!complete) break;
                        for (Field f : e.optional()) {
                            java.util.ArrayDeque<Object> q = queues.get(claimName(f));
                            if (q != null && !q.isEmpty() && fits(f, q.peekFirst(), modes, exactOnly)) {
                                values.put(claimName(f), q.peekFirst());
                                taken++;
                            }
                        }
                        if (taken == 0) break;
                        Map<String, Object> entryData = new LinkedHashMap<>(data);
                        int position = Integer.MAX_VALUE;
                        for (Map.Entry<String, Object> v : values.entrySet()) {
                            String n = v.getKey();
                            entryData.put(n, v.getValue());
                            position = Math.min(position, order.indexOf(n));
                            if ("merge".equals(modes.mode(n))) {
                                pending.get(n).removeAll(((Map<?, ?>) v.getValue()).keySet());
                            } else {
                                queues.get(n).pollFirst();
                            }
                        }
                        written.add(new Written(e.key(), e.fields(), entryData, position, written.size()));
                        progress = true;
                    }
                }
            }
        }
        for (Map.Entry<String, java.util.ArrayDeque<Object>> q : queues.entrySet()) {
            if (!q.getValue().isEmpty()) {
                throw new SchemaException.EncodeException("tlv: no case takes the value of '" + q.getKey()
                        + "' " + q.getValue().peekFirst() + " in full (PS-502, PS-527)");
            }
        }
        for (Map.Entry<String, Set<String>> p : pending.entrySet()) {
            if (!p.getValue().isEmpty()) {
                List<String> left = new ArrayList<>();
                for (String m : p.getValue()) left.add("'" + m + "'");
                java.util.Collections.sort(left);
                throw new SchemaException.EncodeException("tlv: no case takes the members ["
                        + String.join(", ", left) + "] of the merged object '" + p.getKey() + "' in full (PS-527)");
            }
        }
        // A `name_from` field reports under a key built from its template, which no claim
        // above can name. Where the template reads a tag component the tag is not in the
        // data, so no case can take the value - and it was dropped with nothing reported.
        // A key some field declares by name is that field's, not the template's.
        Map<String, Source> declared = null;
        for (Map.Entry<String, List<Field>> e : cases.entrySet()) {
            if (e.getValue() == null) continue;
            List<Claim> claims = new ArrayList<>();
            collectClaims(e.getValue(), false, claims);
            for (Claim c : claims) {
                String template = c.field().getNameFrom();
                if (template == null || template.isEmpty()) continue;
                StringBuilder regex = new StringBuilder();
                String[] parts = template.split("\\$\\{\\w+}", -1);
                for (int i = 0; i < parts.length; i++) {
                    if (i > 0) regex.append(".+");
                    regex.append(java.util.regex.Pattern.quote(parts[i]));
                }
                java.util.regex.Pattern pattern = java.util.regex.Pattern.compile(regex.toString());
                for (Map.Entry<String, Object> d : data.entrySet()) {
                    String key = d.getKey();
                    if (key == null || queues.containsKey(key) || pending.containsKey(key)
                            || !pattern.matcher(key).matches()) continue;
                    if (declared == null) declared = declaredSources(fields);
                    if (declared.containsKey(key)) continue;
                    throw new SchemaException.EncodeException("tlv: no case takes the value of '" + key
                            + "' " + d.getValue() + " in full (PS-502, PS-527)");
                }
            }
        }

        // Payload order, so a payload round-trips; entries of one name keep the order taken.
        written.sort((x, y) -> x.position() != y.position()
                ? Integer.compare(x.position(), y.position()) : Integer.compare(x.seq(), y.seq()));
        ByteBuf out = new ByteBuf();
        for (int entryIndex = 0; entryIndex < written.size(); entryIndex++) {
            Written w = written.get(entryIndex);
            out.write(encodeTLVTag(w.key(), tlv));
            // PS-572: the entry's index in payload order, as its decode recorded it.
            boolean pushed = rec != null && rec.push(tlv.getRaw(), "tlv", entryIndex);
            byte[] value;
            try {
                value = encodeFieldList(w.fields(), w.data());
            } finally {
                if (rec != null) rec.pop(pushed);
            }
            checkPeeks(value);              // PS-551: a tlv value is measured whole
            if (lengthSize > 0) {
                out.write(writeInt(value.length, lengthSize, false));
            }
            out.write(value);
        }
        return out.toArray();
    }

    /**
     * Whether a case's field takes a value (PS-527): an object element supplies exactly
     * the members it declares, and a field takes no value it cannot write.
     */
    private boolean fits(Field f, Object value, OnRepeat.Modes modes, boolean exactOnly) {
        if (f.getType() == FieldType.OBJECT && value instanceof Map<?, ?> object
                && !"merge".equals(modes.mode(claimName(f)))) {
            List<Claim> claims = new ArrayList<>();
            collectClaims(f.getFields(), false, claims);
            Set<String> every = new LinkedHashSet<>();
            for (Claim c : claims) {
                every.add(c.field().getName());
                if (!c.optional() && !object.containsKey(c.field().getName())) return false;
            }
            for (Object k : object.keySet()) {
                if (!every.contains(String.valueOf(k))) return false;
            }
            return true;
        }
        // A mapping is no value for a scalar field, nor a non-mapping for an object: one name
        // may be an object under one tag and a number under another (sensative/strips).
        boolean isMap = value instanceof Map<?, ?>;
        if (isMap && f.getType() != FieldType.OBJECT && f.getType() != FieldType.REPEAT) return false;
        if (f.getType() == FieldType.OBJECT && !isMap
                && !"merge".equals(modes.mode(claimName(f)))) return false;
        if (isLiteral(f)) return Schema.sameLabel(f.getValue(), value);     // PS-359, PS-513
        if (isComputed(f)) return true;     // written from what it is computed from (PS-434)
        boolean[] took = caseTakes(f, value);
        return took[0] && (took[1] || !exactOnly);
    }

    /**
     * Rebuild a TLV tag from the case key that matched it while decoding.
     *
     * <p>The composite form carries the tag values in the key - {@code "[3, 103]"} against
     * {@code tag_key: [channel_id, channel_type]} - so encoding reads them back out and
     * writes each through its own {@code tag_fields} entry. A key using {@code !} or
     * {@code *} (PS-270) names no single tag, so it cannot be encoded.
     */
    private byte[] encodeTLVTag(String caseKey, Field tlv) {
        List<Field> tagFields = tlv.getTagFields();
        Object tagKey = tlv.getTagKey();

        if (tagFields != null && !tagFields.isEmpty() && tagKey != null) {
            String text = caseKey.trim();
            if (text.startsWith("[")) {
                text = text.endsWith("]") ? text.substring(1, text.length() - 1)
                        : text.substring(1);
            }
            String[] parts = text.split(",");
            List<String> names = new ArrayList<>();
            if (tagKey instanceof List<?> keyList) {
                for (Object k : keyList) names.add(String.valueOf(k));
            } else {
                names.add(String.valueOf(tagKey));
            }
            Map<String, Long> values = new LinkedHashMap<>();
            if (parts.length != names.size()) {
                throw new SchemaException.EncodeException("TLV case '" + caseKey
                        + "' does not match tag_key " + names);
            }
            for (int i = 0; i < parts.length; i++) {
                String part = parts[i].trim().replaceAll("^[\"']|[\"']$", "");
                if ("*".equals(part) || part.startsWith("!")) {
                    throw new SchemaException.EncodeException("TLV case '" + caseKey
                            + "' matches a range of tags, so encoding cannot choose one");
                }
                Long parsed = parseIntAny(part);
                if (parsed == null) {
                    throw new SchemaException.EncodeException("TLV case '" + caseKey
                            + "' has a tag element that is not a number: " + part);
                }
                values.put(names.get(i), parsed);
            }
            ByteBuf out = new ByteBuf();
            for (Field tf : tagFields) {
                Long value = values.get(tf.getName());
                if (value == null) {
                    throw new SchemaException.EncodeException("TLV case '" + caseKey
                            + "' gives no value for '" + tf.getName() + "'");
                }
                out.write(encodeField(tf, value));
            }
            return out.toArray();
        }

        int tagSize = tlv.getTagSize() > 0 ? tlv.getTagSize() : 1;
        Long value = parseIntAny(caseKey.trim());
        if (value == null) {
            throw new SchemaException.EncodeException("TLV case '" + caseKey
                    + "' is not a tag value");
        }
        return writeInt(value, tagSize, false);
    }

    /**
     * The field that declared {@code var: <name>}, searched anywhere in the schema.
     */
    private Field fieldDeclaringVar(String varName) {
        Field found = searchForVar(fields, varName);
        if (found != null) return found;
        found = searchForVar(schema.getFields(), varName);
        if (found != null) return found;
        if (schema.getPorts() != null) {
            for (Schema.PortDef port : schema.getPorts().values()) {
                found = searchForVar(port.getFields(), varName);
                if (found != null) return found;
            }
        }
        return null;
    }

    /**
     * What a reference to {@code source} bound when it reported {@code reported} (PS-523):
     * a numeric label as itself, and the number under a text or boolean label, recovered
     * through the lookup or its {@code ${value}} default.
     */
    private static Object boundFromReported(Field source, Object reported) {
        if (source != null && source.getType() == FieldType.ENUM && reported instanceof String name) {
            // CR-2026-132: a reference to an enum bound its code.
            List<Integer> codes = enumCodes(source, name);
            if (codes.size() > 1) throw sharedLabel(source.getName(), name, codes);   // PS-169
            if (!codes.isEmpty()) return (long) codes.get(0);
            if (name.startsWith("unknown(") && name.endsWith(")")) {
                try {
                    return Long.parseLong(name.substring(8, name.length() - 1));
                } catch (NumberFormatException ignored) {
                    return reported;
                }
            }
            return reported;
        }
        if (source == null || source.getLookup() == null || source.getLookup().isEmpty()
                || reported instanceof Number) {
            return reported;
        }
        Object reversed = reverseLookup(reported, source.getLookup(), source.getName());
        String template = Wave5.template(source);
        if (reversed instanceof String text && template != null) {
            Double recovered = Wave5.matchTemplate(template, text);
            if (recovered != null) return recovered;
        }
        return reversed;
    }

    /** The field a name refers to, wherever in the schema it is declared. */
    private Field fieldNamed(String name) {
        // The fields being encoded first: in a ports schema each port may declare the name.
        Field found = searchForName(fields, name);
        if (found != null) return found;
        found = searchForName(schema.getFields(), name);
        if (found != null) return found;
        if (schema.getPorts() != null) {
            for (Schema.PortDef port : schema.getPorts().values()) {
                found = searchForName(port.getFields(), name);
                if (found != null) return found;
            }
        }
        return null;
    }

    private static Field searchForName(List<Field> list, String name) {
        if (list == null) return null;
        for (Field f : list) {
            if (name.equals(f.getName())) return f;
            for (List<Field> nested : nestedFieldLists(f)) {
                Field found = searchForName(nested, name);
                if (found != null) return found;
            }
        }
        return null;
    }

    private static Field searchForVar(List<Field> list, String varName) {
        if (list == null) return null;
        for (Field f : list) {
            if (varName.equals(f.getVar()) && f.getName() != null && !f.getName().isEmpty()) {
                return f;
            }
            for (List<Field> nested : nestedFieldLists(f)) {
                Field found = searchForVar(nested, varName);
                if (found != null) return found;
            }
        }
        return null;
    }

    /** Every field list one field can contain, so a search reaches the whole schema. */
    private static List<List<Field>> nestedFieldLists(Field f) {
        List<List<Field>> out = new ArrayList<>();
        if (f.getFields() != null) out.add(f.getFields());
        if (f.getByteGroup() != null) out.add(f.getByteGroup());
        if (f.getTagFields() != null) out.add(f.getTagFields());
        if (f.getCases() != null) {
            for (Field.Case c : f.getCases()) {
                if (c.getFields() != null) out.add(c.getFields());
            }
        }
        if (f.getTlvCases() != null) out.addAll(f.getTlvCases().values());
        if (f.getFlagged() != null && f.getFlagged().getGroups() != null) {
            for (Field.FlaggedGroup g : f.getFlagged().getGroups()) {
                if (g.getFields() != null) out.add(g.getFields());
            }
        }
        if (f.getMatchInline() != null) out.add(List.of(f.getMatchInline()));
        if (f.getTlvInline() != null) out.add(List.of(f.getTlvInline()));
        return out;
    }

    // --- reversing the modifiers ---------------------------------------------------

    /**
     * Undo what decoding applied, in the opposite order: the lookup, then the transform
     * chain, then the canonical modifiers.
     *
     * <p>The lookup comes first, before the numeric check below. A lookup's whole purpose
     * is to report a label, so the value arriving here is a string; reversing it after a
     * numeric guard leaves the reversal dead code for every label, and the label then
     * reaches integer parsing as "Class A".
     */
    private Object reverseModifiers(Object value, Field field) {
        // The value as given, for a PS-168 rejection to name (PS-552).
        givenField = field;
        givenValue = value;
        Object reversed = reverseLookup(value, field.getLookup(), field.getName());   // PS-169

        if (reversed instanceof Boolean flag && field.getLookup() != null
                && !field.getLookup().isEmpty()) {
            // PS-513: a boolean matches only a boolean label, and none has this one under
            // a key of its own. Where only the `default` carries it, it stands for every
            // unmapped value (PS-269, PS-409): there is nothing to write.
            throw new SchemaException.EncodeException(flag + " is not a label with a key of "
                    + "its own in the lookup for '" + field.getName() + "'; a boolean matches "
                    + "only a boolean label, and a `default` label has no value to recover "
                    + "(PS-409, PS-513)");
        }

        // PS-409: a string matching the ${value} default carries the value it stands for.
        String template = Wave5.template(field);
        if (reversed instanceof String s && template != null) {
            Double recovered = Wave5.matchTemplate(template, s);
            if (recovered == null) {
                throw new SchemaException.EncodeException("'" + s + "' is neither a label in "
                        + "the lookup for '" + field.getName() + "' nor a match for its default '"
                        + template + "' (PS-409)");
            }
            reversed = recovered;
        }

        if (reversed instanceof String && field.getLookup() != null
                && !field.getLookup().isEmpty()) {
            // The label is not in the table, so it came from the mapping's `default`,
            // which stands for every value the table does not list (PS-269) - there is no
            // original to recover.
            throw new SchemaException.EncodeException("'" + reversed
                    + "' is not a label in the lookup for '" + field.getName()
                    + "'; a `default` label matches any unmapped value, so the value that "
                    + "produced it cannot be recovered");
        }

        Double numeric = asDouble(reversed);
        if (numeric == null) {
            return reversed;
        }

        double result = reverseCanonicalModifiers(
                reverseTransformStages(numeric, field.getTransform()), field);

        // The nibble-decimals carry tenths, so they keep their fraction too.
        FieldType ft = field.getType();
        if (ft == FieldType.F32LE16 || ft == FieldType.F32BE16LE || ft == FieldType.UFLT16
                || ft == FieldType.SFLT16 || ft == FieldType.SFLT24) {
            return result;   // these carry fractions too
        }
        if (field.getType().isFloat() || field.getType() == FieldType.UDEC
                || field.getType() == FieldType.SDEC) {
            return result;
        }
        // Half-to-even, matching the reference interpreter's rounding.
        return (long) Math.rint(result);
    }

    /**
     * Map a label back to its integer, comparing by {@link Schema#sameLabel} (PS-513). A
     * boolean label used to be stored as the text "false", so a boolean input never
     * matched it and was written as its own 0 or 1: {@code false} through
     * {@code [true, false]} wrote index 0 (CR-2026-104).
     */
    private static Object reverseLookup(Object value, Map<Integer, Object> lookup) {
        return reverseLookup(value, lookup, null);
    }

    /**
     * {@link #reverseLookup(Object, Map)} rejecting a label two or more codes share, naming
     * the field {@code name} and the codes (PS-169, CR-2026-146). It wrote the first code
     * found. With no {@code name} - a caller only ranking candidate cases - the smallest
     * code is returned instead, as the reference's _case_fidelity takes one.
     */
    private static Object reverseLookup(Object value, Map<Integer, Object> lookup, String name) {
        if (lookup == null || lookup.isEmpty()) return value;
        List<Integer> codes = new ArrayList<>();
        for (Map.Entry<Integer, Object> entry : lookup.entrySet()) {
            if (Schema.sameLabel(entry.getValue(), value)) codes.add(entry.getKey());
        }
        if (codes.isEmpty()) return value;
        java.util.Collections.sort(codes);
        if (codes.size() > 1 && name != null) throw sharedLabel(name, value, codes);
        return Long.valueOf(codes.get(0));
    }

    /** The PS-169 rejection, in the spec prototype's words; the codes ascending. */
    static SchemaException.EncodeException sharedLabel(String name, Object label,
                                                      List<Integer> codes) {
        List<Integer> sorted = new ArrayList<>(codes);
        java.util.Collections.sort(sorted);
        StringBuilder listed = new StringBuilder();
        for (int i = 0; i < sorted.size(); i++) {
            if (i > 0) listed.append(", ");
            listed.append(sorted.get(i));
        }
        return new SchemaException.EncodeException("Field '" + name + "': the label "
                + labelText(label) + " is shared by codes [" + listed
                + "], so it cannot be encoded (PS-169)");
    }

    /**
     * A label as the PS-169 and PS-067 messages write it: text in single quotes, a boolean
     * as JSON writes it, a number as JavaScript's String(number) does.
     */
    static String labelText(Object label) {
        if (label instanceof String text) return "'" + text + "'";
        if (label instanceof Boolean flag) return flag ? "true" : "false";
        return jsNumber(label);
    }

    /**
     * Undo a {@code transform} chain, innermost stage last.
     *
     * <p>Decoding runs the stages in order, so encoding runs their inverses in reverse
     * order. Rounding and clamping stages are identity in reverse: the precision they
     * discarded cannot be recovered, and for a value that was in range they changed
     * nothing. Genuinely irreversible arithmetic throws, so a caller reports the field
     * rather than writing a wrong byte.
     */
    private static double reverseTransformStages(double value, List<Field.Transform> stages) {
        if (stages == null) return value;
        for (int i = stages.size() - 1; i >= 0; i--) {
            Field.Transform stage = stages.get(i);
            if (stage.getAdd() != null) {
                value -= stage.getAdd();
            } else if (stage.getMult() != null) {
                if (stage.getMult() == 0) {
                    throw new SchemaException.EncodeException("cannot undo 'mult: 0'");
                }
                value /= stage.getMult();
            } else if (stage.getDiv() != null) {
                value *= stage.getDiv();
            } else if (Boolean.TRUE.equals(stage.getSqrt())) {
                throw new SchemaException.EncodeException("cannot undo transform stage: sqrt");
            } else if (Boolean.TRUE.equals(stage.getAbs())) {
                throw new SchemaException.EncodeException("cannot undo transform stage: abs");
            } else if (Boolean.TRUE.equals(stage.getLog10())) {
                throw new SchemaException.EncodeException("cannot undo transform stage: log10");
            } else if (Boolean.TRUE.equals(stage.getLog())) {
                throw new SchemaException.EncodeException("cannot undo transform stage: log");
            } else if (stage.getPow() != null) {
                throw new SchemaException.EncodeException("cannot undo transform stage: pow");
            }
            // A rounding or bounding stage - {op: round}, or a `round:` key this parser
            // records nothing of - is identity in reverse.
        }
        return value;
    }

    /**
     * Invert the canonical modifiers. Decoding computes {@code ((raw * mult) / div) + add},
     * so encoding subtracts {@code add}, multiplies by {@code div}, then divides by
     * {@code mult} (PS-101).
     */
    private static double reverseCanonicalModifiers(double value, Field field) {
        if (field.getAdd() != null) value -= field.getAdd();
        if (field.getDiv() != null) value *= field.getDiv();
        if (field.getMult() != null && field.getMult() != 0) value /= field.getMult();
        return value;
    }

    // --- one field's bytes ---------------------------------------------------------

    private byte[] encodeField(Field field, Object value) {
        FieldType type = field.getType();

        if (type == FieldType.BITS) {
            // A bit range outside a byte_group: the reference interpreter writes the value
            // into one byte rather than trying to reconstruct a byte it does not own.
            long raw = asLong(value) == null ? 0 : asLong(value);
            return new byte[]{(byte) (raw & 0xFF)};
        }

        if (type == FieldType.BOOL) {
            boolean set = value instanceof Boolean b ? b
                    : (asLong(value) != null && asLong(value) != 0);
            return new byte[]{(byte) (set ? 1 : 0)};
        }

        if (type == FieldType.ENUM) {
            return encodeEnum(field, value);
        }

        // The word-ordered spellings: least significant 16-bit unit first, each unit
        // big-endian, and `endian` plays no part (PS-271, PS-272). Both are in
        // FieldType.isInteger(), so without this they fell through to the plain integer
        // path below and were written as an ordinary four-byte value in the schema's byte
        // order - the right length, the wrong order, and no error to say so. The decoder
        // has always read them the word-ordered way, so every such field re-encoded to
        // bytes it would not decode back (CR-2026-028).
        //
        // Deliberately not consulting `endian`, for the reason the decoder gives:
        // honouring it would make u32le16 with endian little a second spelling of
        // little-endian u32.
        if (type == FieldType.U32LE16 || type == FieldType.S32LE16) {
            Long raw = asLong(value);
            if (raw == null) {
                throw new SchemaException.EncodeException("field '" + field.getName()
                        + "': expected a number, got " + describeValue(value));
            }
            long[] wb = integerBounds(32, type == FieldType.S32LE16);
            checkFits(raw, wb[0], wb[1], field.getName(), given(field, raw));   // PS-168
            long combined = raw < 0 ? raw + 0x100000000L : raw;
            combined &= 0xFFFFFFFFL;
            ByteBuf wordOrdered = new ByteBuf();
            wordOrdered.write(writeInt(combined & 0xFFFF, 2, false, false));
            wordOrdered.write(writeInt(combined >>> 16, 2, false, false));
            return wordOrdered.toArray();
        }

        if (Wave4.isWordOrdered(type) && type != FieldType.U32LE16 && type != FieldType.S32LE16) {
            Double raw = asDouble(value);
            if (raw == null) {
                throw new SchemaException.EncodeException("field '" + field.getName()
                        + "': expected a number, got " + describeValue(value));
            }
            return Wave4.writeWordOrdered(type, raw);
        }
        if (type == FieldType.UFLT16 || type == FieldType.SFLT16 || type == FieldType.SFLT24) {
            Double raw = asDouble(value);
            if (raw == null) {
                throw new SchemaException.EncodeException("field '" + field.getName()
                        + "': expected a number, got " + describeValue(value));
            }
            boolean useLittle = "little".equalsIgnoreCase(field.getEffectiveEndian(schema.getEndian()));
            return writeInt(Wave4.encodeMinifloat(type, raw), Wave4.minifloatSize(type), false, useLittle);
        }

        if (type.isInteger()) {
            Long raw = asLong(value);
            if (raw == null) {
                throw new SchemaException.EncodeException("field '" + field.getName()
                        + "': expected a number, got " + describeValue(value));
            }
            boolean signed = type.isSigned();
            if (field.getEncoding() != null) {
                // The inverse of the named code, after the modifiers are reversed (PS-463),
                // and after rounding (PS-552). Written unsigned.
                raw = Wave4.encodeEncoding(raw, field.getEncoding(), type.defaultLength(), field.getName());
                signed = false;
            }
            long[] ib = integerBounds(type.defaultLength() * 8, signed);
            checkFits(raw, ib[0], ib[1], field.getName(), given(field, raw));   // PS-168
            // BINT is big-endian whatever the schema says, matching the decoder.
            boolean bigEndianOverride = type == FieldType.BINT;
            String fieldEndian = field.getEffectiveEndian(schema.getEndian());
            boolean useLittle = !bigEndianOverride && "little".equalsIgnoreCase(fieldEndian);
            return writeInt(raw, type.defaultLength(), signed, useLittle);
        }

        if (type.isFloat()) {
            Double raw = asDouble(value);
            if (raw == null) {
                throw new SchemaException.EncodeException("field '" + field.getName()
                        + "': expected a number, got " + describeValue(value));
            }
            raw = raw + 0.0;   // PS-566: -0.0 + 0.0 is 0.0, so every float type writes the zero word
            boolean useLittle = "little".equalsIgnoreCase(
                    field.getEffectiveEndian(schema.getEndian()));
            return switch (type) {
                case F16, FLOAT16 -> writeInt(zeroWord(floatToHalfBits(raw), 0x8000L), 2, false, useLittle);
                case BF16 -> writeInt(Bf16.encode(raw, field.getName()), 2, false, useLittle);   // PS-559
                case F64, FLOAT64 -> writeInt(Double.doubleToLongBits(raw), 8, false, useLittle);
                default -> writeInt(zeroWord(Float.floatToIntBits((float) (double) raw) & 0xFFFFFFFFL,
                        0x80000000L), 4, false, useLittle);
            };
        }

        if (type == FieldType.UDEC || type == FieldType.SDEC) {
            // The inverse of PS-330, as tools/schema_interpreter.py writes it: floor the
            // whole part so the tenths stay non-negative (-1.5 is -2 + 0.5).
            Double raw = asDouble(value);
            if (raw == null) {
                throw new SchemaException.EncodeException("field '" + field.getName()
                        + "': expected a number, got " + describeValue(value));
            }
            // PS-556 (CR-2026-147): n is the value times 10, rounded half to even on the
            // binary64 product (PS-552); the upper nibble is n div 10 and the lower n mod
            // 10, both floored. Rounding the fraction after splitting off the whole part
            // wrote 3.45 as 0x35.
            long n = (long) Math.rint(raw * 10);
            checkNibbleDecimal(n, type == FieldType.SDEC, field.getName(),
                    given(field, value));                                         // PS-168
            long whole = Math.floorDiv(n, 10L);
            return new byte[] {(byte) (((whole & 0x0F) << 4) | Math.floorMod(n, 10L))};
        }

        if (Cr181.isByteString(type)) {
            return Cr181.encode(field, value);       // CR-2026-181: PS-081, PS-601, PS-602
        }

        if ((type == FieldType.STRING || type == FieldType.BOOLEAN) && field.getValue() != null) {
            // A literal came from no bytes, so it writes none. This wrote its text:
            // `{type: string, value: "ppm"}` re-encoded 07 as 70706d07.
            return new byte[0];
        }
        if (type == FieldType.ASCII || type == FieldType.STRING) {
            if (field.getNumeral() != null && type == FieldType.ASCII && asDouble(value) != null) {
                // PS-561: digits in the declared base, rounded as PS-552 requires.
                Object declared = field.getRaw() != null ? field.getRaw().get("length") : null;
                Integer fixed = declared instanceof Integer n && n >= 0 ? n : null;
                return encodeAscii(field, Numeral.encode(field.getName(),
                        (long) Math.rint(asDouble(value)), fixed, field.getNumeral()));
            }
            return encodeAscii(field, String.valueOf(value));
        }

        throw new SchemaException.EncodeException("Cannot encode type: " + type);
    }

    private byte[] encodeEnum(Field field, Object value) {
        String base = field.getBase() == null ? "u8" : field.getBase();
        int size = switch (base) {
            case "u16", "s16" -> 2;
            case "u24", "s24" -> 3;
            case "u32", "s32" -> 4;
            default -> 1;
        };
        boolean signed = base.startsWith("s");
        long raw;
        if (value instanceof String name) {
            // A name back to its code (PS-067). One several codes share names none of them
            // (PS-169); one with no code, the `default` label among them, cannot be
            // recovered (PS-068, PS-554).
            List<Integer> codes = enumCodes(field, name);
            if (codes.isEmpty()) {
                throw new SchemaException.EncodeException("Field '" + field.getName() + "': "
                        + labelText(name) + " is not a name of the enum (PS-067)");
            }
            if (codes.size() > 1) throw sharedLabel(field.getName(), name, codes);
            raw = codes.get(0);
        } else if (value instanceof Number number && !(value instanceof Boolean)) {
            // A code as given, rounded (PS-552) and checked against the base, naming the
            // enum (PS-168): a code past it was reported as "300 does not fit 1 unsigned
            // bytes", naming no field.
            raw = value instanceof Double || value instanceof Float
                    ? (long) Math.rint(number.doubleValue()) : number.longValue();
        } else {
            throw new SchemaException.EncodeException("Field '" + field.getName() + "': "
                    + labelText(value) + " is not a name or code of the enum (PS-067)");
        }
        long[] bounds = integerBounds(size * 8, signed);
        checkFits(raw, bounds[0], bounds[1], field.getName(), given(field, value));
        return writeInt(raw, size, signed);
    }

    /** Every code an enum gives {@code name} as its name, ascending. */
    private static List<Integer> enumCodes(Field field, String name) {
        List<Integer> codes = new ArrayList<>();
        if (field.getValues() != null) {
            for (Map.Entry<Integer, String> e : field.getValues().entrySet()) {
                if (name.equals(e.getValue())) codes.add(e.getKey());
            }
        }
        java.util.Collections.sort(codes);
        return codes;
    }

    /**
     * Parse a {@code bitfield_string} back into its packed integer.
     *
     * <p>Each declared part reads one delimited segment, in the base the part declares.
     * The hex parts are lowercase on output (PS-074), and parsing is case-insensitive.
     */
    private byte[] encodeBitfieldString(Field field, String value) {
        String text = value;
        String prefix = field.getPrefix() == null ? "" : field.getPrefix();
        if (!prefix.isEmpty() && text.startsWith(prefix)) {
            text = text.substring(prefix.length());
        }
        String delimiter = field.getDelimiter() == null ? "." : field.getDelimiter();
        String[] segments = text.split(java.util.regex.Pattern.quote(delimiter), -1);

        long packed = 0;
        List<List<Object>> parts = field.getParts();
        if (parts != null) {
            for (int i = 0; i < parts.size(); i++) {
                List<Object> part = parts.get(i);
                if (part.size() < 2) continue;
                Long bitOff = asLong(part.get(0));
                Long bitLen = asLong(part.get(1));
                if (bitOff == null || bitLen == null) continue;
                Object format = part.size() >= 3 ? part.get(2) : "decimal";
                String segment = i < segments.length ? segments[i] : "0";
                // PS-096, PS-431, PS-583: parsed by the part's format; a value the width
                // cannot hold is rejected, never masked.
                long raw = BitfieldParts.parseSegment(segment, bitLen.intValue(), format, field.getName());
                long mask = bitLen >= 64 ? -1L : (1L << bitLen) - 1;
                packed |= (raw & mask) << bitOff;
            }
        }
        int length = field.getLength() > 0 ? field.getLength() : 2;
        // PS-050a: the field's own byte order, as it is decoded; this wrote the schema's.
        return writeInt(packed, length, false,
                "little".equalsIgnoreCase(field.getEffectiveEndian(schema.getEndian())));
    }

    // --- primitives ----------------------------------------------------------------

    private static final byte[] EMPTY = new byte[0];

    /** The byte count to write for a variable-length field. */
    /**
     * PS-537: a character above U+00FF, or a value longer than a fixed {@code length}, is
     * rejected naming the field; U+0080..U+00FF is written as the byte of the same number
     * with a warning; a short value is padded with 0x00. A length that is not fixed
     * ({@code remaining}, a reference) lets the value supply it. This wrote UTF-8 and
     * truncated, which could split a character ("ABCé" became 414243c3).
     */
    private byte[] encodeAscii(Field field, String text) {
        String name = field.getName();
        for (int i = 0; i < text.length(); i++) {
            // A character outside the BMP is a surrogate pair, each half above U+00FF.
            if (text.charAt(i) > 0xFF) {
                throw new SchemaException.FieldEncodeException("Field '" + name
                        + "': an ascii value holds a character above U+00FF, which has no byte (PS-537)");
            }
        }
        byte[] raw = text.getBytes(StandardCharsets.ISO_8859_1);
        Object declared = field.getRaw() != null ? field.getRaw().get("length") : null;
        Integer fixed = declared instanceof Integer n && n >= 0 ? n : null;
        if (fixed != null && raw.length > fixed) {
            throw new SchemaException.FieldEncodeException(String.format(
                    "Field '%s': an ascii value of %d bytes is longer than its length of %d (PS-537)",
                    name, raw.length, fixed));
        }
        for (byte b : raw) {
            if ((b & 0xFF) > 0x7F) {
                warnings.add("Field '" + name + "': a character above U+007F in an ascii value (PS-537)");
                break;
            }
        }
        return fixed == null ? raw : pad(raw, fixed);
    }

    private static byte[] pad(byte[] raw, int length) {
        if (raw.length == length) return raw;
        byte[] out = new byte[length];
        System.arraycopy(raw, 0, out, 0, Math.min(raw.length, length));
        return out;
    }

    private byte[] writeInt(long value, int size, boolean signed) {
        return writeInt(value, size, signed, little);
    }

    /** The field whose value reverseModifiers last saw, and that value as given. */
    private Field givenField;
    private Object givenValue;

    /** The value the input gave for {@code field}, before its arithmetic was inverted. */
    private Object given(Field field, Object fallback) {
        return field == givenField ? givenValue : fallback;
    }

    /**
     * PS-168, PS-552: the rounded integer fits the field's type, or the input is rejected
     * naming the field and the value given, in the spec prototype's words. The value is
     * written as JavaScript's String(number) writes it, so a cross-language diff compares
     * equal strings. Writing it masked or wrapped put another number on the air.
     */
    static long checkFits(long raw, long low, long high, String name, Object given) {
        if (raw < low || raw > high) {
            throw new SchemaException.EncodeException("Field '" + name + "': "
                    + jsNumber(given) + " encodes as " + raw + ", outside " + low + " to "
                    + high + " (PS-168)");
        }
        return raw;
    }

    /**
     * PS-168 for {@code udec}/{@code sdec} (PS-330): one byte holds a whole part in a nibble
     * and tenths 0 to 9, so 0 to 15.9, or -8 to 7.9 signed. {@code tenths} is the value as
     * encoded, in tenths, and the message gives it to one decimal, in checkFits's form.
     * Both said "does not fit udec", in words of their own.
     */
    static void checkNibbleDecimal(long tenths, boolean signed, String name, Object given) {
        long low = signed ? -80 : 0, high = signed ? 79 : 159;
        if (tenths >= low && tenths <= high) return;
        long abs = Math.abs(tenths);
        String encoded = (tenths < 0 ? "-" : "") + (abs / 10) + (abs % 10 != 0 ? "." + (abs % 10) : "");
        throw new SchemaException.EncodeException("Field '" + name + "': " + jsNumber(given)
                + " encodes as " + encoded + ", outside " + (signed ? "-8" : "0") + " to "
                + (signed ? "7.9" : "15.9") + " (PS-168)");
    }

    /**
     * PS-557 (CR-2026-147): a fractional value given for a discriminator the encoder writes
     * is rejected, naming the match as PS-535 does, and never rounded or truncated.
     */
    static void checkWrittenDiscriminator(Object value, String name) {
        if (!(value instanceof Double || value instanceof Float)) return;
        double d = ((Number) value).doubleValue();
        if (Double.isNaN(d) || Double.isInfinite(d) || d == Math.rint(d)) return;
        String label = name != null && !name.isEmpty() ? "'" + name + "'" : "an unnamed match";
        throw new SchemaException.EncodeException("Field " + label + ": the discriminator "
                + jsNumber(d) + " is not an integer, so it cannot be written (PS-557)");
    }

    /** The range of a whole integer of {@code bits} bits. */
    static long[] integerBounds(int bits, boolean signed) {
        if (bits >= 64) {
            return signed ? new long[] {Long.MIN_VALUE, Long.MAX_VALUE}
                    : new long[] {0, Long.MAX_VALUE};
        }
        return signed ? new long[] {-(1L << (bits - 1)), (1L << (bits - 1)) - 1}
                : new long[] {0, (1L << bits) - 1};
    }

    /**
     * A bit range's type is its width (PS-168, PS-552): the one place the bit-range reading
     * of the rule lives, so it can be adjusted alone.
     */
    private long checkBitRange(long raw, Field member) {
        long[] b = integerBounds(member.getBits(), member.isSignedBits());
        if (raw < b[0] || raw > b[1]) {
            // PS-064 (CR-2026-136): a bit range names its type after the range, as the
            // prototype does: "... outside 0 to 15 for u8[0:3] (PS-168)".
            throw new SchemaException.EncodeException("Field '" + member.getName() + "': "
                    + jsNumber(given(member, raw)) + " encodes as " + raw + ", outside "
                    + b[0] + " to " + b[1] + " for " + bitRangeType(member) + " (PS-168)");
        }
        return raw;
    }

    /** A bit range's type as the schema spells it, e.g. {@code u8[0:3]}. */
    private static String bitRangeType(Field member) {
        Object declared = member.getRaw() == null ? null : member.getRaw().get("type");
        if (declared instanceof String text && text.contains("[")) return text;
        return (member.isSignedBits() ? "s" : "u") + (Math.max(1, member.getBitBaseBytes()) * 8)
                + "[" + member.getBitOffset() + ":" + (member.getBitOffset() + member.getBits() - 1) + "]";
    }

    /** A number as JavaScript's String(number) writes it; anything else as String.valueOf. */
    static String jsNumber(Object value) {
        if (value instanceof Boolean || !(value instanceof Number n)) return String.valueOf(value);
        if (value instanceof Long || value instanceof Integer || value instanceof Short
                || value instanceof Byte || value instanceof java.math.BigInteger) {
            return value.toString();
        }
        double d = n.doubleValue();
        if (d == Math.rint(d) && Math.abs(d) < 1e21) {
            return new java.math.BigDecimal(d).toBigInteger().toString();
        }
        if (Math.abs(d) >= 1e-6 && Math.abs(d) < 1e21) {
            return new java.math.BigDecimal(Double.toString(d)).stripTrailingZeros().toPlainString();
        }
        // Exponent form as JavaScript writes it: 3.4e+38, 1e-7 (Java's is 3.4E38, 1.0E-7).
        String text = Double.toString(d);
        int e = text.indexOf('E');
        if (e < 0) return text;
        String mantissa = text.substring(0, e), exponent = text.substring(e + 1);
        if (mantissa.endsWith(".0")) mantissa = mantissa.substring(0, mantissa.length() - 2);
        return mantissa + "e" + (exponent.startsWith("-") ? "" : "+") + exponent;
    }

    /** PS-566, CR-2026-157: the sign-only word a negative value rounds to is the zero word. */
    static long zeroWord(long word, long signOnly) {
        return word == signOnly ? 0 : word;
    }

    private static byte[] writeInt(long value, int size, boolean signed, boolean little) {
        if (size <= 0) return EMPTY;
        if (size < 8) {
            if (signed) {
                long half = 1L << (8 * size - 1);
                if (value < -half || value >= half) {
                    throw new SchemaException.EncodeException(
                            value + " does not fit " + size + " signed bytes");
                }
            } else if (value < 0 || value > (1L << (8 * size)) - 1) {
                throw new SchemaException.EncodeException(
                        value + " does not fit " + size + " unsigned bytes");
            }
        }
        byte[] out = new byte[size];
        for (int i = 0; i < size; i++) {
            int shift = little ? i * 8 : (size - 1 - i) * 8;
            out[i] = (byte) ((value >>> shift) & 0xFF);
        }
        return out;
    }

    /** Inclusive bounds a field of this type can hold, or null if it is not an integer. */
    private static double[] integerRange(FieldType type) {
        if (type == null || !type.isInteger() || type == FieldType.BITS) return null;
        int size = type.defaultLength();
        if (type.isSigned()) {
            double half = Math.pow(2, 8.0 * size - 1);
            return new double[]{-half, half - 1};
        }
        return new double[]{0, Math.pow(2, 8.0 * size) - 1};
    }

    /** IEEE 754 half-precision bits for a double, round-to-nearest. */
    private static long floatToHalfBits(double value) {
        int bits = Float.floatToIntBits((float) value);
        int sign = (bits >>> 16) & 0x8000;
        int magnitude = bits & 0x7FFFFFFF;
        if (magnitude >= 0x7F800000) {
            // Infinity, or a NaN whose payload is kept non-zero.
            int mantissa = (bits & 0x007FFFFF) >>> 13;
            return sign | 0x7C00 | (magnitude > 0x7F800000 && mantissa == 0 ? 1 : mantissa);
        }
        int rounded = magnitude + 0x1000;
        if (rounded >= 0x47800000) {
            return sign | 0x7BFF;   // clamp to the largest finite half
        }
        if (rounded >= 0x38800000) {
            return sign | ((rounded - 0x38000000) >>> 13);
        }
        if (rounded < 0x33000000) {
            return sign;
        }
        int exponent = magnitude >>> 23;
        return sign | ((((bits & 0x7FFFFF) | 0x800000) + (0x800000 >>> (exponent - 102)))
                >>> (126 - exponent));
    }

    /** A field's name as it appears in decoded output, or null if it never appears. */
    private static String payloadName(Field f) {
        String name = f.getName();
        if (name == null || name.isEmpty() || name.startsWith("_")) return null;
        if (isComputed(f)) return null;
        return name;
    }

    private static Long asLong(Object value) {
        Double d = asDouble(value);
        return d == null ? null : (long) Math.rint(d);
    }

    private static Double asDouble(Object value) {
        if (value instanceof Boolean b) return b ? 1.0 : 0.0;
        if (value instanceof Number n) return n.doubleValue();
        return null;
    }

    /** Read an integer written as decimal, {@code 0x} hex or {@code 0b} binary. */
    private static Long parseIntAny(String text) {
        String trimmed = text.trim().replaceAll("^[\"']|[\"']$", "");
        if (trimmed.isEmpty()) return null;
        boolean negative = trimmed.startsWith("-");
        if (negative) trimmed = trimmed.substring(1);
        try {
            long magnitude;
            if (trimmed.length() > 2 && (trimmed.startsWith("0x") || trimmed.startsWith("0X"))) {
                magnitude = Long.parseLong(trimmed.substring(2), 16);
            } else if (trimmed.length() > 2
                    && (trimmed.startsWith("0b") || trimmed.startsWith("0B"))) {
                magnitude = Long.parseLong(trimmed.substring(2), 2);
            } else {
                magnitude = Long.parseLong(trimmed);
            }
            return negative ? -magnitude : magnitude;
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static String describeValue(Object value) {
        return value == null ? "null" : value.getClass().getSimpleName() + " " + value;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> stringKeyed(Map<?, ?> raw) {
        boolean alreadyStrings = true;
        for (Object key : raw.keySet()) {
            if (!(key instanceof String)) {
                alreadyStrings = false;
                break;
            }
        }
        if (alreadyStrings) return (Map<String, Object>) raw;
        Map<String, Object> out = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : raw.entrySet()) {
            out.put(String.valueOf(entry.getKey()), entry.getValue());
        }
        return out;
    }

    /** A growable byte sink; the encoders append to one of these rather than concatenate. */
    /**
     * The bytes a field list writes, laid out as a decoder reads them (CR-2026-136).
     *
     * <p>A bit range or a bool that does not {@code consume} leaves the bytes it filled
     * pending: a decoder peeks them (PS-059, PS-066), so the next field reads the same
     * bytes, and its own bits or bytes are ORed into them. Bytes still pending at the end
     * are written too, since a decoder reads them. Mirrors the reference's FrameBuffer.
     */
    private static final class FrameBuffer {
        private final ByteBuf buf = new ByteBuf();
        private byte[] pending = new byte[0];
        // CR-2026-141 (PS-551): the bits written, by position in this list's frame, and
        // each bit range or bool placed.
        private byte[] covered = new byte[0];
        private final List<Peek> peeks = new ArrayList<>();
        private final List<Echo> echoes = new ArrayList<>();      // PS-589

        /**
         * PS-589 (CR-2026-175): a non-consuming byte string writes nothing and is not
         * required; a value given for it is checked where the frame closes. It reads from
         * where a decoder is, the start of any bytes still pending.
         */
        void echo(Field field, Map<String, Object> data) {
            String name = field.getName();
            if (name == null || data.get(name) == null) return;
            echoes.add(new Echo(buf.size, field, data.get(name), echoLength(field, data)));
        }

        private void cover(int at, int mask) {
            if (covered.length <= at) {
                covered = java.util.Arrays.copyOf(covered, Math.max(at + 1, covered.length * 2));
            }
            covered[at] = (byte) (covered[at] | mask);
        }

        FrameMeta meta() {
            int total = buf.size + pending.length;
            return new FrameMeta(java.util.Arrays.copyOf(covered, total), new ArrayList<>(peeks),
                    new ArrayList<>(echoes));
        }

        byte[] pending() {
            return pending.clone();
        }

        int pendingLength() {
            return pending.length;
        }

        /** The bytes written and pending (PS-564's measure). */
        int size() {
            return buf.size + pending.length;
        }

        void extend(byte[] data) {
            extend(data, 0, null);
        }

        void extend(byte[] data, int carried) {
            extend(data, carried, null);
        }

        /**
         * OR {@code data} over the pending bytes and write it, keeping its last
         * {@code carried} bytes pending - a nested list's unconsumed tail - ORed with any
         * older pending bytes beyond them.
         */
        void extend(byte[] data, int carried, FrameMeta meta) {
            if (meta != null) for (Echo e : meta.echoes()) echoes.add(e.shifted(buf.size));
            if (data == null || data.length == 0) return;
            carried = Math.min(carried, data.length);
            int offset = buf.size;
            byte[] dataCovered = meta == null ? null : meta.covered();
            int shared0 = Math.min(pending.length, data.length);
            for (int i = 0; i < shared0; i++) {
                int cov = dataCovered == null ? 0xFF : dataCovered[i] & 0xFF;
                if ((pending[i] & ~data[i] & cov & 0xFF) != 0) {
                    // PS-551: a field would OR over a peek's different bits.
                    byte[] frame = new byte[offset + data.length];
                    System.arraycopy(buf.buffer, 0, frame, 0, offset);
                    System.arraycopy(data, 0, frame, offset, data.length);
                    throw new SchemaException.EncodeException(peekDisagreement(peeks, frame));
                }
            }
            for (int i = 0; i < data.length; i++) {
                cover(offset + i, dataCovered == null ? 0xFF : dataCovered[i] & 0xFF);
            }
            if (meta != null) for (Peek p : meta.peeks()) peeks.add(p.shifted(offset));
            if (pending.length > 0) {
                data = data.clone();
                int shared = Math.min(pending.length, data.length);
                for (int i = 0; i < shared; i++) data[i] = (byte) (data[i] | pending[i]);
                pending = java.util.Arrays.copyOfRange(pending, shared, pending.length);
            }
            if (carried > 0) {
                buf.write(java.util.Arrays.copyOfRange(data, 0, data.length - carried));
                byte[] tail = java.util.Arrays.copyOfRange(data, data.length - carried, data.length);
                if (pending.length > tail.length) tail = java.util.Arrays.copyOf(tail, pending.length);
                for (int i = 0; i < pending.length; i++) tail[i] = (byte) (tail[i] | pending[i]);
                pending = tail;
                return;
            }
            buf.write(data);
        }

        /** OR {@code bits} at {@code start} into the pending base; then consume (PS-016). */
        void placeBits(int baseSize, boolean little, int start, long bits, int consume) {
            placeBits(baseSize, little, start, bits, consume, 0, null, false);
        }

        /**
         * CR-2026-141 (PS-551): a named range of {@code width} bits is recorded for the
         * check where the frame closes; a derived one writes no bits.
         */
        void placeBits(int baseSize, boolean little, int start, long bits, int consume,
                       int width, String name, boolean derived) {
            if (width > 0 && name != null && !name.isEmpty()) {
                Peek peek = new Peek(buf.size, baseSize, little, start, width,
                        derived ? null : bits, name);
                peeks.add(peek);
                if (!derived) {
                    for (int i = 0; i < baseSize; i++) cover(buf.size + i, peek.maskByte(i));
                }
            }
            if (pending.length < baseSize) pending = java.util.Arrays.copyOf(pending, baseSize);
            long base = 0;
            for (int i = 0; i < baseSize; i++) {
                int b = pending[little ? baseSize - 1 - i : i] & 0xFF;
                base = (base << 8) | b;
            }
            base |= bits << start;
            for (int i = 0; i < baseSize; i++) {
                int shift = (baseSize - 1 - i) * 8;
                pending[little ? baseSize - 1 - i : i] = (byte) ((base >>> shift) & 0xFF);
            }
            if (consume > 0) {
                if (pending.length < consume) pending = java.util.Arrays.copyOf(pending, consume);
                buf.write(java.util.Arrays.copyOfRange(pending, 0, consume));
                pending = java.util.Arrays.copyOfRange(pending, consume, pending.length);
            }
        }

        byte[] frame() {
            buf.write(pending);
            pending = new byte[0];
            return buf.toArray();
        }
    }

    private static final class ByteBuf {
        private byte[] buffer = new byte[64];
        private int size;

        void write(byte[] bytes) {
            if (bytes == null || bytes.length == 0) return;
            if (size + bytes.length > buffer.length) {
                byte[] grown = new byte[Math.max(buffer.length * 2, size + bytes.length)];
                System.arraycopy(buffer, 0, grown, 0, size);
                buffer = grown;
            }
            System.arraycopy(bytes, 0, buffer, size, bytes.length);
            size += bytes.length;
        }

        byte[] toArray() {
            byte[] out = new byte[size];
            System.arraycopy(buffer, 0, out, 0, size);
            return out;
        }
    }
}
