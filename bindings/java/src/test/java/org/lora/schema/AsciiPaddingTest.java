package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * An {@code ascii} value drops its trailing NUL padding and nothing else, as Python, Go,
 * C# and the generated codec do. This binding removed every NUL and trimmed whitespace at
 * both ends, so {@code " H\0C\0"} read as {@code "HC"} here and {@code " H\0C"} elsewhere.
 */
public class AsciiPaddingTest {

    @Test
    public void onlyTrailingNulsAreDropped() {
        Schema s = Schema.fromYaml("name: p\nfields:\n- {name: label, type: ascii, length: 5}\n");
        Map<String, Object> out = s.decode(new byte[] {' ', 'H', 0, 'C', 0});
        assertEquals(" H\0C", out.get("label"));
    }
}
