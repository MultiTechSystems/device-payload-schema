package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

/** CR-2026-044: a ragged tail under `until: end` is an error naming the repeat (PS-343 to PS-344a). */
public class CR2026044Test {
    @Test
    public void aRaggedTailIsAnError() {
        Schema schema = Schema.fromYaml("name: p\nfields:\n"
            + "- {name: r, type: repeat, until: end, fields: [{name: a, type: u8}, {name: b, type: u16}]}\n");
        Exception e = assertThrows(SchemaException.class, () -> schema.decode(new byte[]{1, 0, 2, 2}));
        assertTrue(e.getMessage().contains("'r' ends in a ragged tail")
                && e.getMessage().contains("fewer than the 3"), e.getMessage());
        assertEquals(2, ((List<?>) schema.decode(new byte[]{1, 0, 2, 2, 0, 3}).get("r")).size());
    }
}
