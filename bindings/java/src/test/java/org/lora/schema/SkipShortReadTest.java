package org.lora.schema;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * A skip that runs past the end of the payload is a short read like any other. Python and
 * the generated codec decoded it with success; this pins that Java does not.
 */
public class SkipShortReadTest {

    @Test
    public void aSkipPastTheEndIsAnError() {
        Schema s = Schema.fromYaml("name: p\nfields:\n- {name: a, type: u8}\n"
                + "- {name: pad, type: skip, length: 4}\n");
        assertThrows(RuntimeException.class, () -> s.decode(new byte[] {1, 2}));
    }
}
