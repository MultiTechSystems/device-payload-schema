package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/** CR-2026-107: a boolean literal (PS-357, PS-358 amended; PS-519, PS-520). */
public class CR2026107Test {
    @Test
    public void malformedBooleanLiteralsAreRefused() {
        String[][] cases = {
            {"PS-519", "{name: k, type: boolean}"},
            {"PS-520", "{name: k, type: boolean, value: null}"},
            {"PS-358", "{name: k, type: boolean, value: true, mult: 2}"},
            {"PS-520", "{name: k, type: boolean, value: \"true\"}"},
            {"PS-520", "{name: k, type: boolean, value: 1}"},
            {"PS-358", "{name: k, type: boolean, value: true, lookup: {1: x}}"},
        };
        for (String[] c : cases) {
            Exception e = assertThrows(SchemaException.class,
                    () -> Schema.fromYaml("name: p\nfields:\n  - " + c[1] + "\n"), c[1]);
            assertTrue(e.getMessage().contains(c[0]), c[1] + ": " + e.getMessage());
        }
    }

    @Test
    public void aBooleanLiteralWorksInEveryFieldList() {
        String[][] cases = {
            {"top", "  - {name: a, type: u8}\n  - {name: hw, type: boolean, value: true}\n  - {name: b, type: u8}\n", "0105"},
            {"match", "  - {name: kind, type: u8}\n  - match:\n      field: $kind\n      cases:\n        1:\n"
                + "          - {name: hw, type: boolean, value: true}\n          - {name: b, type: u8}\n", "0105"},
            {"tlv", "  - tlv:\n      tag_size: 1\n      cases:\n        1:\n"
                + "          - {name: hw, type: boolean, value: true}\n          - {name: b, type: u8}\n", "0105"},
            {"object", "  - name: o\n    type: object\n    fields:\n      - {name: hw, type: boolean, value: true}\n"
                + "      - {name: b, type: u8}\n", "05"},
            {"repeat", "  - name: r\n    type: repeat\n    count: 2\n    fields:\n"
                + "      - {name: hw, type: boolean, value: true}\n      - {name: b, type: u8}\n", "0506"},
        };
        for (String[] c : cases) {
            Schema schema = Schema.fromYaml("name: p\nfields:\n" + c[1]);
            byte[] payload = hex(c[2]);
            Map<String, Object> out = schema.decode(payload);
            Object hw = switch (c[0]) {
                case "object" -> ((Map<?, ?>) out.get("o")).get("hw");
                case "repeat" -> ((Map<?, ?>) ((List<?>) out.get("r")).get(1)).get("hw");
                default -> out.get("hw");
            };
            assertEquals(Boolean.TRUE, hw, c[0] + ": " + out);
            assertArrayEquals(payload, schema.encode(out).getPayload(), c[0] + " (PS-359)");
        }
    }

    private static byte[] hex(String s) {
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        }
        return out;
    }
}
