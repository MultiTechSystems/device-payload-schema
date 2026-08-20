package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * PS-301 to PS-303 (CR-2026-013): an unknown TLV tag has to be visible.
 *
 * <p>This binding had no warning channel on the decode side at all - encoding reported
 * warnings through {@link EncodeResult}, decoding reported nothing - so a payload that
 * stopped at an undescribed tag came back as a successful decode carrying fewer fields,
 * indistinguishable from a device that sent fewer fields. {@code raw} behaved as
 * {@code skip}, building no entry.
 */
public class CR2026013Test {

    /** Tag 0x01 carries a u16 of 60; tag 0x09 is not described. Tag-only, so nothing
     * delimits the unknown entry and the two trailing bytes cannot be reached. */
    private static final String TAG_ONLY = "01003C090BB8";
    /** The same with a length byte after each tag, so the entry can be stepped over. */
    private static final String DELIMITED = "0102003C09020BB8";

    private static byte[] bytes(String hex) {
        byte[] raw = new byte[hex.length() / 2];
        for (int i = 0; i < raw.length; i++) {
            raw[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        }
        return raw;
    }

    private static Map<String, Object> decode(String mode, int lengthSize, String hex) {
        StringBuilder yaml = new StringBuilder(
            "name: t\nendian: big\nfields:\n  - tlv:\n      tag_size: 1\n");
        if (mode != null) {
            yaml.append("      unknown: ").append(mode).append("\n");
        }
        if (lengthSize > 0) {
            yaml.append("      length_size: 1\n");
        }
        yaml.append("      cases:\n        1:\n          - {name: known, type: u16}\n");
        return Schema.fromYaml(yaml.toString()).decode(bytes(hex));
    }

    @SuppressWarnings("unchecked")
    private static List<String> warnings(Map<String, Object> result) {
        Object value = result.get("_warnings");
        return value instanceof List ? (List<String>) value : List.of();
    }

    @Test
    void stoppingShortIsReported() {
        // PS-301, PS-302. The fields before the tag are reported unchanged, which is why
        // this is a warning and not an error.
        Map<String, Object> result = decode("skip", 0, TAG_ONLY);
        assertEquals(60L, ((Number) result.get("known")).longValue());
        assertEquals(
            List.of("unknown TLV tag (0x09) at offset 3: 3 of 6 byte(s) left undecoded"),
            warnings(result));
    }

    @Test
    void skipIsWhatHappensWithoutTheParameter() {
        assertEquals(warnings(decode(null, 0, TAG_ONLY)),
                     warnings(decode("skip", 0, TAG_ONLY)));
    }

    @Test
    void aDelimitedEntryIsSteppedOverAndReported() {
        Map<String, Object> result = decode("skip", 1, DELIMITED);
        assertEquals(60L, ((Number) result.get("known")).longValue());
        assertEquals(List.of("unknown TLV tag (0x09) skipped, 2 byte(s) discarded"),
                     warnings(result));
    }

    @Test
    void aCleanDecodeCarriesNoWarningKey() {
        assertFalse(decode("skip", 0, "01003C").containsKey("_warnings"));
    }

    @Test
    void errorModeFailsNamingTheTag() {
        SchemaException.DecodeException thrown = assertThrows(
            SchemaException.DecodeException.class, () -> decode("error", 0, TAG_ONLY));
        assertTrue(thrown.getMessage().contains("0x09"), thrown.getMessage());
    }

    @Test
    @SuppressWarnings("unchecked")
    void rawReportsItsEntryWhenOutputIsMerged() {
        // PS-303. `merge` defaults to true, which is every schema that does not set it.
        Map<String, Object> result = decode("raw", 0, TAG_ONLY);
        List<Object> entries = (List<Object>) result.get("unknown_tags");
        assertEquals(1, entries.size());
        assertEquals("0bb8", ((Map<String, Object>) entries.get(0)).get("raw"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void aDelimitedRawCaptureTakesOnlyItsOwnBytes() {
        Map<String, Object> result = decode("raw", 1, DELIMITED);
        List<Object> entries = (List<Object>) result.get("unknown_tags");
        assertEquals(1, entries.size());
        assertEquals("0bb8", ((Map<String, Object>) entries.get(0)).get("raw"));
        assertTrue(warnings(result).isEmpty(), "the entry was delimited");
    }

    @Test
    void theWarningNamesEveryTagComponent() {
        // The Milesight shape: channel then type. Both are needed to add the missing case.
        String yaml = "name: t\nendian: big\nfields:\n  - tlv:\n"
                    + "      tag_fields:\n        - {name: channel, type: u8}\n"
                    + "        - {name: kind, type: u8}\n"
                    + "      tag_key: [channel, kind]\n"
                    + "      cases:\n        \"[1, 117]\":\n"
                    + "          - {name: battery, type: u8}\n";
        Map<String, Object> result = Schema.fromYaml(yaml).decode(bytes("0175640569"));
        assertEquals(1, warnings(result).size());
        assertTrue(warnings(result).get(0).contains("0x05, 0x69"), warnings(result).toString());
    }
}
