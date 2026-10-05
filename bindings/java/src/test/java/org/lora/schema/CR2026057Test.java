package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.HexFormat;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

/**
 * CR-2026-057: a zero divisor (PS-100) and the log of a non-positive number (PS-117)
 * leave the field absent, never NaN (PS-282), and the later fields still decode.
 */
public class CR2026057Test {

    @Test
    public void arithmeticWithNoValueOmitsTheField() {
        String[][] cases = {
            {"bare div 0", "{name: v, type: u8, div: 0}", "07"},
            {"stage div 0", "{name: v, type: u8, transform: [{div: 0}]}", "07"},
            {"log10 of 0", "{name: v, type: u8, transform: [{log10: true}]}", "00"},
            {"log of -1", "{name: v, type: s8, transform: [{log: true}]}", "FF"},
            {"log then lookup", "{name: v, type: u8, transform: [{log: true}], lookup: {0: zero}}", "00"},
        };
        for (String[] c : cases) {
            Schema schema = Schema.fromYaml("name: p\nfields:\n- " + c[1] + "\n- {name: w, type: u8}\n");
            Map<String, Object> out = schema.decode(HexFormat.of().parseHex(c[2] + "05"));
            assertFalse(out.containsKey("v"), c[0] + ": v = " + out.get("v"));
            assertEquals(5L, ((Number) out.get("w")).longValue(), c[0]);
        }
    }
}
