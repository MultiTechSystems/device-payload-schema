package org.lora.schema;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-114 (an ascii value: PS-534, PS-537, PS-538) and CR-2026-115 (a read past the
 * end names the field, PS-535), la-payload-schema main c932961.
 *
 * <p>A short read and a rejected encode are failures, which no corpus vector expects, so
 * their exact messages are pinned here; they are the spec prototype's text and the same
 * in all five implementations. The decodes are held by
 * {@code _language-conformance/ascii-latin1.yaml}.
 */
public class CR2026114115Test {

    private static String schema(String fields) {
        return "name: probe\nendian: big\nfields:\n" + fields;
    }

    private static String underflow(String yaml, String hex) {
        SchemaException.DecodeException e = assertThrows(SchemaException.DecodeException.class,
                () -> Schema.fromYaml(yaml).decode(HexFormat.of().parseHex(hex)));
        return e.getMessage();
    }

    static Stream<Arguments> shortReads() {
        String need = "Buffer underflow: need %d bytes at offset %d, but only %d remaining (PS-535)";
        return Stream.of(
            Arguments.of(schema("- {name: n, type: u16}\n"), "01",
                "Field 'n': " + String.format(need, 2, 0, 1)),
            Arguments.of(schema("- {type: skip, length: 3}\n"), "",
                "Field an unnamed skip: " + String.format(need, 3, 0, 0)),
            Arguments.of(schema("- {name: pad, type: skip, length: 3}\n"), "00",
                "Field 'pad': " + String.format(need, 3, 0, 1)),
            Arguments.of(schema("- {name: s, type: ascii, length: 4}\n"), "4142",
                "Field 's': " + String.format(need, 4, 0, 2)),
            Arguments.of(schema("- {name: h, type: hex, length: 4}\n"), "41",
                "Field 'h': " + String.format(need, 4, 0, 1)),
            Arguments.of(schema("- {name: b, type: bytes, length: 4}\n"), "41",
                "Field 'b': " + String.format(need, 4, 0, 1)),
            Arguments.of(schema("- {name: f, type: f32}\n"), "0102",
                "Field 'f': " + String.format(need, 4, 0, 2)),
            Arguments.of(schema("- {name: _k, type: u16}\n"), "01",
                "Field '_k': " + String.format(need, 2, 0, 1)),
            Arguments.of(schema("- {name: w, type: u32le16}\n"), "0102",
                "Field 'w': " + String.format(need, 4, 0, 2)),
            Arguments.of(schema("- {name: ud, type: udec}\n"), "",
                "Field 'ud': " + String.format(need, 1, 0, 0)),
            // The innermost field is named, not the object or repeat holding it.
            Arguments.of(schema("- name: o\n  type: object\n  fields:\n  - {name: x, type: u16}\n"),
                "01", "Field 'x': " + String.format(need, 2, 0, 1)),
            Arguments.of(schema("- name: r\n  type: repeat\n  count: 2\n  fields:\n"
                    + "  - {name: v, type: u16}\n"),
                "000102", "Field 'v': " + String.format(need, 2, 2, 1)),
            // CR-2026-111: an element is decoded as a one-field record named `element`.
            Arguments.of(schema("- {name: r, type: repeat, count: 2, element: {name: v, type: u16}}\n"),
                "000102", "Field 'v': " + String.format(need, 2, 2, 1)),
            Arguments.of(schema("- {name: r, type: repeat, count: 2, element: {type: u16}}\n"),
                "000102", "Field 'element': " + String.format(need, 2, 2, 1)),
            Arguments.of(schema("- {name: k, type: u8, var: k}\n- match:\n    field: $k\n"
                    + "    cases:\n      1:\n      - {name: m, type: u16}\n"),
                "0101", "Field 'm': " + String.format(need, 2, 1, 1)),
            Arguments.of(schema("- tlv:\n    tag_size: 1\n    cases:\n      1:\n"
                    + "      - {name: t, type: u16}\n"),
                "0101", "Field 't': " + String.format(need, 2, 1, 1)),
            Arguments.of(schema("- {name: fl, type: u8}\n- flagged:\n    field: fl\n    groups:\n"
                    + "    - bit: 0\n      fields:\n      - {name: g, type: u16}\n"),
                "0101", "Field 'g': " + String.format(need, 2, 1, 1)),
            // A reserve narrows the end (PS-535 "less any reserve").
            Arguments.of(schema("- tlv:\n    tag_size: 1\n    reserve: 1\n    cases:\n      1:\n"
                    + "      - {name: t, type: u16}\n"),
                "010102", "Field 't': " + String.format(need, 2, 1, 1)),
            Arguments.of(schema("- {name: n, type: u8}\n- {name: s, type: ascii, length: $n}\n"),
                "0541", "Field 's': " + String.format(need, 5, 1, 1)),
            // The enum is named, not its base.
            Arguments.of(schema("- {name: e, type: enum, base: u16, values: {1: one}}\n"), "00",
                "Field 'e': " + String.format(need, 2, 0, 1)),
            // An inline discriminator is named by the match's `name`, else what it is.
            Arguments.of(schema("- match:\n    length: 2\n    name: kind\n    cases:\n      1:\n"
                    + "      - {name: m, type: u8}\n"),
                "00", "Field 'kind': " + String.format(need, 2, 0, 1)),
            Arguments.of(schema("- match:\n    length: 2\n    cases:\n      1:\n"
                    + "      - {name: m, type: u8}\n"),
                "00", "Field an unnamed match: " + String.format(need, 2, 0, 1)),
            // A bit range and a bool read without consuming: the peek form.
            Arguments.of(schema("- {name: b, type: 'u16[0:3]', consume: 2}\n"), "01",
                "Field 'b': Buffer underflow at peek offset 0 (PS-535)"),
            Arguments.of(schema("- {name: bo, type: bool, bit: 1}\n"), "",
                "Field 'bo': Buffer underflow at peek offset 0 (PS-535)"),
            // A `consume` past the end is a read like any other: the need form.
            Arguments.of(schema("- {name: b, type: 'u8[0:3]', consume: 2}\n"), "01",
                "Field 'b': " + String.format(need, 2, 0, 1)),
            Arguments.of(schema("- {name: bo, type: bool, consume: 2}\n"), "01",
                "Field 'bo': " + String.format(need, 2, 0, 1)),
            Arguments.of(schema("- byte_group:\n    size: 2\n    fields:\n"
                    + "    - {name: a, type: 'u16[0:3]'}\n    - {name: c, type: 'u16[4:7]'}\n"),
                "01", "Field byte_group of 'a', 'c': Buffer underflow at peek offset 0 (PS-535)"),
            // CR-2026-131: the group is one read, named by every member in order, internal
            // ones included, whichever member's base would have fitted.
            Arguments.of(schema("- byte_group:\n    size: 2\n    fields:\n"
                    + "    - {name: a, type: 'u8[0:3]'}\n    - {name: _r, type: 'u8[4:7]'}\n"),
                "01", "Field byte_group of 'a', '_r': Buffer underflow at peek offset 0 (PS-535)"),
            Arguments.of(schema("- byte_group:\n    size: 2\n    fields:\n"
                    + "    - {type: 'u8[0:3]'}\n"),
                "01", "Field an unnamed byte_group: Buffer underflow at peek offset 0 (PS-535)")
        );
    }

    @ParameterizedTest
    @MethodSource("shortReads")
    public void aShortReadNamesTheField(String yaml, String hex, String expected) {
        assertEquals(expected, underflow(yaml, hex));
    }

    @Test
    public void remainingAtTheEndIsNotAnError() {
        Map<String, Object> out = Schema.fromYaml(schema(
                "- {name: a, type: u8}\n- {name: rest, type: hex, length: remaining}\n"))
                .decode(HexFormat.of().parseHex("01"));
        assertEquals("", out.get("rest"));
    }

    @Test
    public void aRaggedTailStillNamesTheRepeat() {
        // PS-343: an until-end element cut short is the repeat's ragged tail.
        String message = underflow(schema("- name: r\n  type: repeat\n  until: end\n  fields:\n"
                + "  - {name: v, type: u16}\n"), "000102");
        assertTrue(message.contains("PS-343"), message);
        assertFalse(message.contains("PS-535"), message);
    }

    // --- CR-2026-114 ---------------------------------------------------------------

    private static final String LABEL = schema("- {name: label, type: ascii, length: 6}\n");

    private static List<String> warnings(Map<String, Object> out) {
        List<String> found = new ArrayList<>();
        if (out.get("_warnings") instanceof List<?> list) {
            for (Object w : list) found.add(String.valueOf(w));
        }
        return found;
    }

    @Test
    public void trailingNulsOnlyAreDropped() {
        Map<String, Object> out = Schema.fromYaml(LABEL).decode(HexFormat.of().parseHex("414200430000"));
        assertEquals("AB\u0000C", out.get("label"));
        out = Schema.fromYaml(LABEL).decode(HexFormat.of().parseHex("204120000000"));
        assertEquals(" A ", out.get("label"));
        out = Schema.fromYaml(LABEL).decode(new byte[6]);
        assertEquals("", out.get("label"));
    }

    @Test
    public void aHighByteIsItsLatin1CharacterWithAWarning() {
        Map<String, Object> out = Schema.fromYaml(LABEL).decode(HexFormat.of().parseHex("41e900000000"));
        assertEquals("Aé", out.get("label"));
        assertEquals(List.of("Field 'label': a byte above 0x7F in an ascii value (PS-073, PS-538)"),
                warnings(out));
    }

    private static EncodeResult encode(String value) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("label", value);
        return Schema.fromYaml(LABEL).encode(data);
    }

    @Test
    public void aShortValueIsPadded() {
        EncodeResult r = encode("AB");
        assertEquals("414200000000", HexFormat.of().formatHex(r.getPayload()));
        assertTrue(r.getErrors().isEmpty() && r.getWarnings().isEmpty(), r.toString());
    }

    @Test
    public void aLatin1CharacterEncodesBackWithAWarning() {
        EncodeResult r = encode("ABCé");
        assertEquals("414243e90000", HexFormat.of().formatHex(r.getPayload()));
        assertEquals(List.of("Field 'label': a character above U+007F in an ascii value (PS-537)"),
                r.getWarnings());
        // Lossless: the bytes decode back to the value.
        Map<String, Object> out = Schema.fromYaml(LABEL).decode(r.getPayload());
        assertEquals("ABCé", out.get("label"));
    }

    @Test
    public void aValueTooLongIsRejected() {
        EncodeResult r = encode("ABCDEFG");
        assertEquals(List.of("Field 'label': an ascii value of 7 bytes is longer than its length of 6 (PS-537)"),
                r.getErrors());
    }

    @Test
    public void aCharacterAboveU00FFIsRejected() {
        EncodeResult r = encode("€");
        assertEquals(List.of("Field 'label': an ascii value holds a character above U+00FF, which has no byte (PS-537)"),
                r.getErrors());
        // Outside the BMP: a surrogate pair, never split into bytes.
        r = encode("A😀");
        assertEquals(List.of("Field 'label': an ascii value holds a character above U+00FF, which has no byte (PS-537)"),
                r.getErrors());
    }
}
