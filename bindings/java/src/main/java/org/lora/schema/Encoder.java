// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
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

    Encoder(Schema schema, List<Field> fields) {
        this.schema = schema;
        this.fields = fields == null ? List.of() : fields;
        this.little = "little".equalsIgnoreCase(schema.getEndian());
    }

    // --- entry point ---------------------------------------------------------------

    EncodeResult run(Map<String, Object> data) {
        Map<String, Object> input = data == null ? Map.of() : data;
        ByteBuf out = new ByteBuf();

        // A `flagged` block's flags byte is not in the decoded output as a number a
        // caller would supply: it is implied by which groups are present. Computed here
        // so the plain field that carries it gets the right value when its turn comes.
        Map<String, Integer> flagsPatches = new LinkedHashMap<>();
        for (Field field : fields) {
            if (field.getFlagged() == null) continue;
            Field.FlaggedDef fd = field.getFlagged();
            int flags = 0;
            for (Field.FlaggedGroup group : fd.getGroups()) {
                if (groupHasData(group, input)) {
                    flags |= (1 << group.getBit());
                }
            }
            if (fd.getField() != null) {
                flagsPatches.put(fd.getField(), flags);
            }
        }

        // Bit ranges sharing a byte are packed together rather than a byte apiece
        // (CR-2026-024), so the whole list is walked at once: a run cannot be found by
        // looking at one field in isolation.
        out.write(encodeWithBitfieldRuns(fields, input, flagsPatches, true));

        return new EncodeResult(out.toArray(), warnings, errors);
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
     * @param topLevel whether a missing value is worth a warning; a construct's body
     *                 supplies zero silently, as the reference interpreter does
     */
    private byte[] encodeOne(Field field, Map<String, Object> data,
                             Map<String, Integer> flagsPatches, boolean topLevel) {
        if (field.getTlvInline() != null) {
            return encodeTLV(field.getTlvInline(), data);
        }
        if (field.getMatchInline() != null) {
            return encodeMatch(field.getMatchInline(), data);
        }
        if (field.getByteGroup() != null) {
            return encodeByteGroup(field, data);
        }
        if (field.getFlagged() != null) {
            return encodeFlagged(field.getFlagged(), data);
        }

        FieldType type = field.getType();

        if (type == FieldType.REPEAT) {
            return encodeRepeat(field, data);
        }
        if (type == FieldType.OBJECT) {
            // A nested object's fields are written in place; decoding reports them
            // flattened, so they are looked up by their own names.
            return encodeFieldList(field.getFields(), data);
        }
        if (type == FieldType.NUMBER) {
            // Derived from other fields: no bytes of its own.
            return EMPTY;
        }
        if (type == FieldType.SKIP) {
            // `remaining` gives no count to pad on encode (PS-014).
            int length = field.getLength() > 0 ? field.getLength() : 0;
            return new byte[length];
        }

        String name = field.getName();

        if (type == FieldType.BITFIELD_STRING) {
            Object value = name == null ? null : data.get(name);
            return encodeBitfieldString(field, value == null ? "" : String.valueOf(value));
        }

        Object value;
        if (name == null || name.isEmpty() || name.startsWith("_")) {
            value = 0L;
        } else if (flagsPatches.containsKey(name)) {
            value = flagsPatches.get(name);
        } else if (data.containsKey(name)) {
            value = data.get(name);
        } else {
            if (topLevel) {
                warnings.add("Missing field: " + name);
            }
            value = 0L;
        }

        return encodeField(field, reverseModifiers(value, field));
    }

    /** Encode a list of fields - a TLV case's value bytes, a match case's body. */
    private byte[] encodeFieldList(List<Field> list, Map<String, Object> data) {
        if (list == null || list.isEmpty()) return EMPTY;
        return encodeWithBitfieldRuns(list, data, Map.of(), false);
    }

    // --- constructs ----------------------------------------------------------------

    private static boolean groupHasData(Field.FlaggedGroup group, Map<String, Object> data) {
        for (Field gf : group.getFields()) {
            if (gf.getName() != null && data.containsKey(gf.getName())) {
                return true;
            }
        }
        return false;
    }

    /** Encode the groups whose fields the data carries; the absent ones cost no bytes. */
    private byte[] encodeFlagged(Field.FlaggedDef fd, Map<String, Object> data) {
        ByteBuf out = new ByteBuf();
        for (Field.FlaggedGroup group : fd.getGroups()) {
            if (!groupHasData(group, data)) continue;
            // Routed through the run packing for consistency with every other field
            // list, not because anything needs it today: no `flagged` group in the corpus
            // holds a bit range - they are u16, u32le16 and computed members. A group
            // that grows one will pack correctly rather than silently not.
            List<Field> emit = new ArrayList<>();
            for (Field gf : group.getFields()) {
                String name = gf.getName();
                if (name == null || name.isEmpty() || name.startsWith("_")) continue;
                if (gf.getType() == FieldType.NUMBER) continue;
                emit.add(gf);
            }
            out.write(encodeWithBitfieldRuns(emit, data, Map.of(), false));
        }
        return out.toArray();
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
                                          Map<String, Integer> patches, boolean topLevel) {
        ByteBuf out = new ByteBuf();
        List<Field> run = new ArrayList<>();
        for (Field field : list) {
            if (!isBareBitfield(field)) {
                flushRun(out, run, data, topLevel);
                try {
                    out.write(encodeOne(field, data, patches, topLevel));
                } catch (RuntimeException e) {
                    if (!topLevel) throw e;
                    errors.add("Error encoding " + describe(field) + ": " + e.getMessage());
                }
                continue;
            }
            run.add(field);
            // `consume` of 1 or more closes the span, which is where the run ends.
            if (field.getConsume() >= 1) {
                flushRun(out, run, data, topLevel);
            }
        }
        flushRun(out, run, data, topLevel);
        return out.toArray();
    }

    /** Emit a pending run, if any, and clear it. */
    private void flushRun(ByteBuf out, List<Field> run, Map<String, Object> data,
                          boolean topLevel) {
        if (run.isEmpty()) return;
        try {
            out.write(encodeBitfieldRun(run, data));
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

    /** Pack one run of bit ranges into the byte or bytes they share. */
    private byte[] encodeBitfieldRun(List<Field> run, Map<String, Object> data) {
        long packed = 0;
        int size = 1;
        for (Field member : run) {
            String name = member.getName();
            Object value = (name == null || name.isEmpty() || name.startsWith("_"))
                    ? Long.valueOf(0)
                    : data.getOrDefault(name, Long.valueOf(0));
            value = reverseModifiers(value, member);
            Long raw = asLong(value);
            if (raw == null) {
                // A label on a bit range: recover the number rather than writing zero,
                // which would be the silent wrong answer this fix exists to remove.
                raw = bitfieldLabelValue(value, member);
            }
            size = Math.max(size, Math.max(1, Math.max(member.getBitBaseBytes(),
                    member.getConsume())));
            long mask = member.getBits() >= 64 ? -1L : (1L << member.getBits()) - 1;
            packed |= (raw & mask) << member.getBitOffset();
        }
        return writeInt(packed, size, false);
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
            Object value = (name == null || name.isEmpty() || name.startsWith("_"))
                    ? Long.valueOf(0)
                    : data.getOrDefault(name, Long.valueOf(0));
            value = reverseModifiers(value, member);
            Long raw = asLong(value);
            if (raw == null) continue;
            if (member.getBits() > 0) {
                // The base width is part of the member's type: u24[4:23] owns three bytes
                // of the group, however small the group's own `size` says it is.
                size = Math.max(size, Math.max(1, member.getBitBaseBytes()));
                long mask = member.getBits() >= 64 ? -1L : (1L << member.getBits()) - 1;
                packed |= (raw & mask) << member.getBitOffset();
            } else {
                // A full-width member owns the group's bytes outright.
                packed |= raw;
            }
        }
        return writeInt(packed, size, false);
    }

    /**
     * Encode a {@code repeat}: its records back to back, and nothing else.
     *
     * <p>The framing costs no bytes here. {@code count: $n} and {@code byte_length: $len}
     * name a field earlier in the list, which the main loop encodes from its own value,
     * and {@code until: end} needs no header at all.
     */
    @SuppressWarnings("unchecked")
    private byte[] encodeRepeat(Field field, Map<String, Object> data) {
        String name = field.getName();
        Object records = name == null ? null : data.get(name);
        if (records == null) return EMPTY;

        List<Object> list;
        if (records instanceof Map<?, ?>) {
            list = List.of(records);
        } else if (records instanceof List<?> asList) {
            list = (List<Object>) asList;
        } else {
            throw new SchemaException.EncodeException("repeat field '" + name
                    + "': expected a list of records, got " + records.getClass().getSimpleName());
        }

        ByteBuf out = new ByteBuf();
        for (Object record : list) {
            if (!(record instanceof Map<?, ?> asMap)) {
                throw new SchemaException.EncodeException("repeat field '" + name
                        + "': expected each record to be a mapping, got "
                        + record.getClass().getSimpleName());
            }
            out.write(encodeFieldList(field.getFields(), stringKeyed(asMap)));
        }
        return out.toArray();
    }

    /**
     * Rebuild a {@code match} construct's bytes from decoded output.
     *
     * <p>Two sources of the discriminator, encoded differently. An inline match with
     * {@code length: N} read those bytes itself, so they are written back. A match on
     * {@code field: $var} read nothing, so writing the discriminator here would duplicate
     * the earlier field that produced it.
     */
    private byte[] encodeMatch(Field match, Map<String, Object> data) {
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
                discriminator = data.get(varName);
            } else {
                // The variable's name is often not the field's: rbs30x has
                // `name: event_type` with `var: evt`, and matches on `$evt`. Decoded
                // output is keyed by the field name, so encoding has to get from one to
                // the other - and undo the field's lookup, since the output holds a label.
                Field source = fieldDeclaringVar(varName);
                if (source != null && source.getName() != null
                        && data.containsKey(source.getName())) {
                    discriminator = reverseLookup(data.get(source.getName()), source.getLookup());
                }
            }
        }

        Field.Case matched = null;
        if (discriminator != null) {
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
        List<Field> fallbackFields = null;
        if (matched == null && discriminator != null) {
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
        if (matched == null && fallbackFields == null) {
            // An inline match with no name reports nothing of itself, so the case has to
            // be recovered from which of its fields the data carries.
            matched = casePresent(cases, data);
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
            // Nothing in the data belongs to any case.
            return EMPTY;
        }
        if (fallbackFields != null) {
            ByteBuf fallbackOut = new ByteBuf();
            if (match.getLength() > 0) {
                Long value = asLong(discriminator);
                fallbackOut.write(writeInt(value == null ? 0 : value,
                        match.getLength(), false));
            }
            fallbackOut.write(encodeFieldList(fallbackFields, data));
            return fallbackOut.toArray();
        }

        ByteBuf out = new ByteBuf();
        if (match.getLength() > 0) {
            Long value = asLong(discriminator);
            if (value == null) {
                Object key = matched.getCaseValue();
                Long fromKey = key == null ? null : parseIntAny(String.valueOf(key));
                if (fromKey == null) {
                    throw new SchemaException.EncodeException("match case " + key
                            + " names no single discriminator value");
                }
                value = fromKey;
            }
            out.write(writeInt(value, match.getLength(), false));
        }
        out.write(encodeFieldList(matched.getFields(), data));
        return out.toArray();
    }

    /** The case whose fields the data carries most of, or null. */
    private static Field.Case casePresent(List<Field.Case> cases, Map<String, Object> data) {
        Field.Case best = null;
        int bestHits = 0;
        for (Field.Case c : cases) {
            if (c.isDefault() || c.getFields() == null) continue;
            int hits = 0;
            for (Field f : c.getFields()) {
                if (payloadName(f) != null && data.containsKey(f.getName())) hits++;
            }
            if (hits > bestHits) {
                best = c;
                bestHits = hits;
            }
        }
        return best;
    }

    private static boolean caseMatches(Object discriminator, Object caseValue) {
        if (caseValue == null) return false;
        Long value = asLong(discriminator);
        if (value == null) {
            return String.valueOf(discriminator).equals(String.valueOf(caseValue));
        }
        if (caseValue instanceof Number n) {
            return value == n.longValue();
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

    private Fidelity caseFidelity(List<Field> caseFields, Map<String, Object> data) {
        int matches = 0;
        boolean lossless = true;
        for (Field f : claimableFields(caseFields)) {
            String name = payloadName(f);
            if (name == null || !data.containsKey(name)) continue;
            matches++;
            Object raw = reverseLookup(data.get(name), f.getLookup());
            Double numeric = asDouble(raw);
            if (numeric == null) continue;
            double value;
            try {
                value = reverseCanonicalModifiers(
                        reverseTransformStages(numeric, f.getTransform()), f);
            } catch (RuntimeException e) {
                lossless = false;
                continue;
            }
            if (Math.abs(value - Math.rint(value)) > 1e-9) {
                lossless = false;
            }
            double[] bounds = integerRange(f.getType());
            if (bounds != null && (Math.rint(value) < bounds[0] || Math.rint(value) > bounds[1])) {
                // It does not fit the field, so this case cannot have written it.
                lossless = false;
            }
        }
        return new Fidelity(matches, lossless);
    }

    private record Fidelity(int matches, boolean lossless) {}

    private record Candidate(int position, int lossPenalty, int matches, String caseKey,
                             List<Field> caseFields, List<String> claimed) {}

    /**
     * Rebuild a TLV payload from decoded output.
     *
     * <p>A case whose fields are all absent is not emitted. A case that cannot be encoded -
     * a wildcard tag (PS-270), or a field the data does not carry - throws, so the caller
     * records it against the payload rather than writing a wrong tag.
     */
    private byte[] encodeTLV(Field tlv, Map<String, Object> data) {
        Map<String, List<Field>> cases = tlv.getTlvCases();
        if (cases == null || cases.isEmpty()) return EMPTY;
        int lengthSize = Math.max(0, tlv.getLengthSize());

        List<String> order = new ArrayList<>(data.keySet());

        List<Candidate> candidates = new ArrayList<>();
        for (Map.Entry<String, List<Field>> entry : cases.entrySet()) {
            if ("default".equals(entry.getKey()) || entry.getValue() == null) continue;
            List<String> claimed = new ArrayList<>();
            for (Field f : claimableFields(entry.getValue())) {
                String name = payloadName(f);
                if (name != null && data.containsKey(name)) claimed.add(name);
            }
            if (claimed.isEmpty()) continue;
            int position = Integer.MAX_VALUE;
            for (String name : claimed) {
                position = Math.min(position, order.indexOf(name));
            }
            Fidelity fidelity = caseFidelity(entry.getValue(), data);
            candidates.add(new Candidate(position, fidelity.lossless() ? 0 : 1,
                    fidelity.matches(), entry.getKey(), entry.getValue(), claimed));
        }

        // Payload order first, then the case that can reproduce the value, then the fuller
        // explanation of it.
        candidates.sort((a, b) -> {
            if (a.position() != b.position()) return Integer.compare(a.position(), b.position());
            if (a.lossPenalty() != b.lossPenalty()) {
                return Integer.compare(a.lossPenalty(), b.lossPenalty());
            }
            return Integer.compare(b.matches(), a.matches());
        });

        // Every decoded field belongs to one channel. Without this a name defined under
        // two tags emits both of them, so am308 grows an extra channel.
        Set<String> spent = new LinkedHashSet<>();
        List<Candidate> emitted = new ArrayList<>();
        for (Candidate candidate : candidates) {
            if (spent.containsAll(candidate.claimed())) continue;
            spent.addAll(candidate.claimed());
            emitted.add(candidate);
        }
        emitted.sort((a, b) -> Integer.compare(a.position(), b.position()));

        ByteBuf out = new ByteBuf();
        for (Candidate candidate : emitted) {
            byte[] tag = encodeTLVTag(candidate.caseKey(), tlv);
            byte[] value = encodeFieldList(candidate.caseFields(), data);
            out.write(tag);
            if (lengthSize > 0) {
                out.write(writeInt(value.length, lengthSize, false));
            }
            out.write(value);
        }
        return out.toArray();
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
        Field found = searchForVar(schema.getFields(), varName);
        if (found != null) return found;
        if (schema.getPorts() != null) {
            for (Schema.PortDef port : schema.getPorts().values()) {
                found = searchForVar(port.getFields(), varName);
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
        Object reversed = reverseLookup(value, field.getLookup());

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

        if (field.getType().isFloat()) {
            return result;
        }
        // Half-to-even, matching the reference interpreter's rounding.
        return (long) Math.rint(result);
    }

    /** Map a label back to its integer. */
    private static Object reverseLookup(Object value, Map<Integer, String> lookup) {
        if (lookup == null || lookup.isEmpty()) return value;
        for (Map.Entry<Integer, String> entry : lookup.entrySet()) {
            if (entry.getValue() != null && entry.getValue().equals(value)) {
                return Long.valueOf(entry.getKey());
            }
        }
        return value;
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

        if (type.isInteger()) {
            Long raw = asLong(value);
            if (raw == null) {
                throw new SchemaException.EncodeException("field '" + field.getName()
                        + "': expected a number, got " + describeValue(value));
            }
            // BINT is big-endian whatever the schema says, matching the decoder.
            boolean bigEndianOverride = type == FieldType.BINT;
            String fieldEndian = field.getEffectiveEndian(schema.getEndian());
            boolean useLittle = !bigEndianOverride && "little".equalsIgnoreCase(fieldEndian);
            return writeInt(raw, type.defaultLength(), type.isSigned(), useLittle);
        }

        if (type.isFloat()) {
            Double raw = asDouble(value);
            if (raw == null) {
                throw new SchemaException.EncodeException("field '" + field.getName()
                        + "': expected a number, got " + describeValue(value));
            }
            boolean useLittle = "little".equalsIgnoreCase(
                    field.getEffectiveEndian(schema.getEndian()));
            return switch (type) {
                case F16, FLOAT16 -> writeInt(floatToHalfBits(raw), 2, false, useLittle);
                case F64, FLOAT64 -> writeInt(Double.doubleToLongBits(raw), 8, false, useLittle);
                default -> writeInt(Float.floatToIntBits((float) (double) raw) & 0xFFFFFFFFL,
                        4, false, useLittle);
            };
        }

        if (type == FieldType.BYTES || type == FieldType.HEX) {
            byte[] raw = toBytes(field, value);
            return pad(raw, encodeLength(field, raw.length));
        }

        if (type == FieldType.ASCII || type == FieldType.STRING) {
            byte[] raw = String.valueOf(value).getBytes(StandardCharsets.UTF_8);
            return pad(raw, encodeLength(field, raw.length));
        }

        if (type == FieldType.BASE64) {
            byte[] raw = Base64.getDecoder().decode(String.valueOf(value));
            int length = field.getLength() > 0 ? field.getLength() : raw.length;
            return pad(raw, length);
        }

        throw new SchemaException.EncodeException("Cannot encode type: " + type);
    }

    private byte[] encodeEnum(Field field, Object value) {
        int size = switch (field.getBase() == null ? "u8" : field.getBase()) {
            case "u16", "s16" -> 2;
            case "u24", "s24" -> 3;
            case "u32", "s32" -> 4;
            default -> 1;
        };
        Map<Integer, String> values = field.getValues();
        if (values != null) {
            for (Map.Entry<Integer, String> entry : values.entrySet()) {
                if (entry.getValue() != null && entry.getValue().equals(value)) {
                    return writeInt(entry.getKey(), size, false);
                }
            }
        }
        Long raw = asLong(value);
        if (raw == null) {
            // The label came from `default`, which stands for every unmapped value
            // (PS-068), so there is no original to recover.
            throw new SchemaException.EncodeException("enum field '" + field.getName()
                    + "': '" + value + "' is not one of its declared values");
        }
        return writeInt(raw, size, false);
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
                String format = part.size() >= 3 ? String.valueOf(part.get(2)) : "decimal";
                String segment = i < segments.length ? segments[i].trim() : "0";
                long raw;
                try {
                    raw = "hex".equals(format) ? Long.parseLong(segment, 16)
                            : Long.parseLong(segment);
                } catch (NumberFormatException e) {
                    throw new SchemaException.EncodeException("bitfield_string field '"
                            + field.getName() + "': segment '" + segment + "' is not "
                            + format);
                }
                long mask = bitLen >= 64 ? -1L : (1L << bitLen) - 1;
                packed |= (raw & mask) << bitOff;
            }
        }
        int length = field.getLength() > 0 ? field.getLength() : 2;
        return writeInt(packed, length, false);
    }

    // --- primitives ----------------------------------------------------------------

    private static final byte[] EMPTY = new byte[0];

    /** The byte count to write for a variable-length field. */
    private static int encodeLength(Field field, int natural) {
        // `length: remaining` has no fixed count when encoding (PS-014) - the value
        // supplies it. It arrives as the negative sentinel the parser stores.
        int declared = field.getLength();
        return declared > 0 ? declared : Math.max(0, natural);
    }

    private static byte[] pad(byte[] raw, int length) {
        if (raw.length == length) return raw;
        byte[] out = new byte[length];
        System.arraycopy(raw, 0, out, 0, Math.min(raw.length, length));
        return out;
    }

    private byte[] toBytes(Field field, Object value) {
        if (value instanceof byte[] raw) return raw;
        if (value instanceof List<?> list) {
            byte[] out = new byte[list.size()];
            for (int i = 0; i < list.size(); i++) {
                Long item = asLong(list.get(i));
                out[i] = (byte) (item == null ? 0 : item & 0xFF);
            }
            return out;
        }
        // CR-2026-008/PS-281 makes the decoder report a byte sequence as a lowercase hex
        // string, so that is the form encoding has to accept for a round trip.
        String text = String.valueOf(value).replace(" ", "").replace(":", "");
        if (text.length() % 2 != 0) {
            throw new SchemaException.EncodeException("field '" + field.getName()
                    + "': expected hex, got '" + value + "' (odd number of digits)");
        }
        byte[] out = new byte[text.length() / 2];
        for (int i = 0; i < out.length; i++) {
            int hi = Character.digit(text.charAt(i * 2), 16);
            int lo = Character.digit(text.charAt(i * 2 + 1), 16);
            if (hi < 0 || lo < 0) {
                throw new SchemaException.EncodeException("field '" + field.getName()
                        + "': expected hex, got '" + value + "'");
            }
            out[i] = (byte) ((hi << 4) | lo);
        }
        return out;
    }

    private byte[] writeInt(long value, int size, boolean signed) {
        return writeInt(value, size, signed, little);
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
        if (f.getType() == FieldType.NUMBER) return null;
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
