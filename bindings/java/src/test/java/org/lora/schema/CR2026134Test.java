package org.lora.schema;

import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-134 (PS-551): an encoder rejects input that omits a required value, naming the
 * field, and derives what the rest of the input fixes - a repeat's count or byte_length
 * field, a match's discriminator, a flagged mask - rejecting a supplied value that
 * disagrees. The cases are tests/cr-2026-134-cases.yaml, shared with the reference, the
 * generated codec, Go and C#, so all five are held to the same bytes and the same text.
 */
public class CR2026134Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-134-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    @TestFactory
    @SuppressWarnings("unchecked")
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                // JSON is a subset of YAML, so the input reads with the same parser.
                Map<String, Object> input = new Yaml().load(String.valueOf(c.get("input")));
                EncodeResult result = schema.encode(input);
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                            "want '" + want + "', got " + result.getErrors());
                    // A rejected input produces no payload, not the bytes written before it.
                    assertEquals(0, result.getPayload().length, result.toString());
                } else {
                    assertTrue(result.isSuccess(), result.getErrors().toString());
                    assertEquals(String.valueOf(c.get("payload")),
                            HexFormat.of().formatHex(result.getPayload()));
                    assertTrue(result.getWarnings().isEmpty(), result.getWarnings().toString());
                }
            }));
        }
        return tests;
    }

    @Test
    public void noMissingFieldWarningIsProduced() {
        EncodeResult result = Schema.fromYaml("name: t\nfields:\n  - {name: a, type: u8}\n"
                + "  - {name: b, type: u8}\n").encode(Map.of("b", 1));
        assertTrue(result.getWarnings().stream().noneMatch(w -> w.contains("Missing field")),
                result.getWarnings().toString());
        assertEquals(List.of("Missing required field: a"), result.getErrors());
    }
}
