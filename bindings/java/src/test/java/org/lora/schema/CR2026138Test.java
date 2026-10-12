package org.lora.schema;

import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.TestFactory;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-138 (PS-553): a field has a name; padding is a skip. A schema with a nameless
 * field other than a skip is refused at load, naming the field by its position in the spec
 * prototype's text; constructs, byte_group members and a repeat's element stay nameless.
 * The cases are tests/cr-2026-138-cases.yaml, shared with the reference, Go and C#.
 */
public class CR2026138Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-138-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    /** Numbers compared by value, so a decoded Long equals a parsed Integer. */
    private static Object normalize(Object value) {
        if (value instanceof Number n) return n.doubleValue();
        if (value instanceof Map<?, ?> m) {
            Map<String, Object> out = new LinkedHashMap<>();
            m.forEach((k, v) -> out.put(String.valueOf(k), normalize(v)));
            return out;
        }
        if (value instanceof List<?> l) {
            List<Object> out = new ArrayList<>();
            for (Object v : l) out.add(normalize(v));
            return out;
        }
        return value;
    }

    @TestFactory
    @SuppressWarnings("unchecked")
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                String text = String.valueOf(c.get("schema"));
                if (c.containsKey("error")) {
                    SchemaException e = assertThrows(SchemaException.class,
                            () -> Schema.fromYaml(text));
                    assertEquals(String.valueOf(c.get("error")), e.getMessage());
                    return;
                }
                Schema schema = Schema.fromYaml(text);
                byte[] payload = HexFormat.of().parseHex(String.valueOf(c.get("payload")));
                Map<String, Object> decoded = schema.decode(payload);
                Map<String, Object> expected = new Yaml().load(String.valueOf(c.get("expected")));
                assertEquals(normalize(expected), normalize(decoded));
                EncodeResult encoded = schema.encode(decoded);
                assertTrue(encoded.isSuccess(), encoded.getErrors().toString());
                assertEquals(String.valueOf(c.get("payload")),
                        HexFormat.of().formatHex(encoded.getPayload()));
            }));
        }
        return tests;
    }
}
