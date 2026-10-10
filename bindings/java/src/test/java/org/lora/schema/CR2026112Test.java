package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/** CR-2026-112 (PS-532): how a name_from template writes the value it substitutes. */
public class CR2026112Test {
    @Test
    public void valuesAreWrittenAsTheGeneratedCodecWritesThem() {
        assertEquals("true", Wave5.renderTemplateValue(Boolean.TRUE));
        assertEquals("false", Wave5.renderTemplateValue(Boolean.FALSE));
        assertEquals("2", Wave5.renderTemplateValue(2.0));
        assertEquals("2.5", Wave5.renderTemplateValue(2.5));
        assertEquals("1e-7", Wave5.renderTemplateValue(1e-7));
        assertEquals("1.5e+21", Wave5.renderTemplateValue(1.5e21));
        assertEquals("-3", Wave5.renderTemplateValue(-3L));
        assertEquals("boost", Wave5.renderTemplateValue("boost"));
        assertNull(Wave5.renderTemplateValue(Map.of("a", 1)));
    }

    @Test
    public void anObjectOrRepeatReferenceIsRefusedAtLoad() {
        for (String kind : new String[] {"object", "repeat\n    count: 1"}) {
            Exception e = assertThrows(SchemaException.class, () -> Schema.fromYaml(
                    "name: bad\nfields:\n  - name: grp\n    type: " + kind
                    + "\n    fields:\n      - {name: a, type: u8}\n"
                    + "  - {name: v, type: u8, name_from: \"v_${grp}\"}\n"), kind);
            assertTrue(e.getMessage().contains("PS-532"), e.getMessage());
        }
    }

    @Test
    public void aVariableAndABooleanRoundTripThroughTheirKey() {
        Schema schema = Schema.fromYaml("name: enc\nendian: big\nfields:\n"
                + "  - name: side\n    type: u8\n    var: at\n    lookup: {1: left, 2: right}\n"
                + "  - {name: wet, type: bool, bit: 0, consume: 1}\n"
                + "  - {name: reading, type: u8, name_from: \"r_${at}_${wet}\"}\n");
        byte[] payload = {0x02, 0x01, 0x05};
        Map<String, Object> out = schema.decode(payload);
        assertTrue(out.containsKey("r_right_true"), out.toString());
        assertArrayEquals(payload, schema.encode(out).getPayload());
    }
}
