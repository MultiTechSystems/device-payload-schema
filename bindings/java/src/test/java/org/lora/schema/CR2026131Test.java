package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * CR-2026-131 (PS-070): a byte_group covers its widest member's base, {@code size} may
 * widen it and a narrower {@code size} is rejected at load; a member is a bit range, a
 * whole integer or a bool with {@code bit}, each read from the group's start. The messages
 * are the spec prototype's.
 */
public class CR2026131Test {

    private static byte[] hex(String text) {
        return HexFormat.of().parseHex(text);
    }

    private static String rejection(String fields) {
        return assertThrows(SchemaException.class,
                () -> Schema.fromYaml("name: t\nfields:\n" + fields)).getMessage();
    }

    @Test
    public void aSizeNarrowerThanAMemberIsRejected() {
        String want = "byte_group declares size 5, narrower than its widest member's 8 bytes (PS-070)";
        assertEquals(want, rejection(
                "- byte_group: {size: 5, fields: [{name: a, type: 'u64[0:3]'}]}\n"));
        assertEquals(want, rejection("- byte_group: [{name: a, type: 'u64[0:3]'}]\n  size: 5\n"));
    }

    @Test
    public void aNarrowSizeInATlvCaseIsRejected() {
        String message = rejection("- tlv:\n    tag_size: 1\n    cases:\n      1:\n"
                + "        - byte_group: {size: 1, fields: [{name: a, type: 'u16[0:3]'}]}\n");
        assertTrue(message.contains(
                "byte_group declares size 1, narrower than its widest member's 2 bytes (PS-070)"), message);
    }

    @Test
    public void memberKindsAreChecked() {
        assertEquals("byte_group members 'w' and 'f' overlap (PS-397)",
                rejection("- byte_group: [{name: w, type: u8}, {name: f, type: bool, bit: 0}]\n"));
        assertEquals("byte_group member 'f' is a bool at bit 9; a bool member is one bit of a u8, 0 to 7 (PS-070)",
                rejection("- byte_group: [{name: f, type: bool, bit: 9}]\n"));
        assertEquals("byte_group member 'f' is not a bit range, a whole integer or a bool with `bit` (PS-364)",
                rejection("- byte_group: [{name: f, type: bool}]\n"));
    }

    @Test
    public void aWholeIntegerReadsFromTheGroupsStart() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n"
                + "- byte_group: {endian: little, fields: [{name: lo, type: u8}, {name: hi, type: 'u16[8:11]'}]}\n"
                + "- {name: n, type: u8}\n");
        Map<String, Object> out = schema.decode(hex("050307"));
        assertEquals(5L, ((Number) out.get("lo")).longValue());
        assertEquals(3L, ((Number) out.get("hi")).longValue());
        assertEquals(7L, ((Number) out.get("n")).longValue());
        Map<String, Object> data = new LinkedHashMap<>(Map.of("lo", 5, "hi", 3, "n", 7));
        assertEquals("050307", HexFormat.of().formatHex(schema.encode(data).getPayload()));
    }

    @Test
    public void aSizelessGroupIsItsWidestBase() {
        Schema schema = Schema.fromYaml("name: t\nfields:\n"
                + "- byte_group: [{name: lo, type: 'u16[0:3]'}]\n- {name: n, type: u8}\n");
        Map<String, Object> out = schema.decode(hex("A00507"));
        assertEquals(5L, ((Number) out.get("lo")).longValue());
        assertEquals(7L, ((Number) out.get("n")).longValue());
    }
}
