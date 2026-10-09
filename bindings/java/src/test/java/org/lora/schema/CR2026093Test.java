package org.lora.schema;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import java.util.HexFormat;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-093 (PS-486): a tlv entry is never partly read. A length cut short, or a length
 * declaring more bytes than remain, is an error naming the tlv and the entry's offset, for
 * a known tag and for an unknown one that would be skipped. Mirrors
 * tests/test_cr_2026_093.py, whose cases are the CR's own.
 */
public class CR2026093Test {

    private static String schema(int lengthSize, int reserve) {
        return "name: probe\nfields:\n- tlv:\n    tag_size: 1\n    length_size: " + lengthSize + "\n"
                + (reserve > 0 ? "    reserve: " + reserve + "\n" : "")
                + "    cases:\n      1: [{name: a, type: u8}]\n"
                + "      2: [{name: b, type: bytes, length: remaining}]\n";
    }

    @ParameterizedTest(name = "{0}")
    @CsvSource(delimiter = '|', value = {
        "length byte missing|1|0|01010702|tlv entry at offset 3: 0 byte(s) remain after its tag, fewer than its 1-byte length (PS-486)",
        "value cut short|1|0|0101070205aabb|tlv entry at offset 3: its length declares 5 byte(s), 2 remain (PS-486)",
        "unknown tag skipped past the end|1|0|0101070905aabb|tlv entry at offset 3: its length declares 5 byte(s), 2 remain (PS-486)",
        "one of two length bytes|2|0|010001070200|tlv entry at offset 4: 1 byte(s) remain after its tag, fewer than its 2-byte length (PS-486)",
        "value cut short by a reserve|1|1|0101070205aabb|tlv entry at offset 3: its length declares 5 byte(s), 1 remain (PS-486)",
    })
    void aCutShortEntryIsAnError(String label, int lengthSize, int reserve, String hex, String message) {
        Schema s = Schema.fromYaml(schema(lengthSize, reserve));
        Exception e = assertThrows(Exception.class, () -> s.decode(HexFormat.of().parseHex(hex)));
        assertTrue(e.getMessage().contains(message), e.getMessage());
    }

    @ParameterizedTest
    @CsvSource({"010107,", "0101070202aabb,aabb"})
    void anEntryThatFitsDecodes(String hex, String b) {
        Map<String, Object> out = Schema.fromYaml(schema(1, 0)).decode(HexFormat.of().parseHex(hex));
        assertEquals(7L, ((Number) out.get("a")).longValue());
        assertEquals(b, out.get("b"));
        assertNull(out.get("_warnings"));
    }
}
