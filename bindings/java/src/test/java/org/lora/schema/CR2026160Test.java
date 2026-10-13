package org.lora.schema;

import org.junit.jupiter.api.DynamicTest;
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
 * CR-2026-160 (0.5.6, PS-059): u40/u48/u56 and s40/s48/s56 are bit-range bases only. A standalone
 * {@code type: u56} is rejected at load; a bit range over one is read.
 * The cases are shared with the reference, Go and C# in tests/cr-2026-160-cases.yaml.
 */
public class CR2026160Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-160-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    /** A canonical text: keys in order, every number as a double. */
    private static String norm(Object v) {
        if (v instanceof Number n) return String.valueOf(n.doubleValue());
        if (v instanceof Map<?, ?> m) {
            StringBuilder sb = new StringBuilder("{");
            for (Map.Entry<?, ?> e : m.entrySet()) sb.append(e.getKey()).append('=').append(norm(e.getValue())).append(',');
            return sb.append('}').toString();
        }
        if (v instanceof List<?> l) {
            StringBuilder sb = new StringBuilder("[");
            for (Object o : l) sb.append(norm(o)).append(',');
            return sb.append(']').toString();
        }
        return String.valueOf(v);
    }

    @TestFactory
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                String text = String.valueOf(c.get("schema"));
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(text));
                    assertTrue(e.getMessage().contains(want), "want '" + want + "', got " + e.getMessage());
                    return;
                }
                Schema schema = Schema.fromYaml(text);
                Map<String, Object> out = schema.decode(HexFormat.of().parseHex(String.valueOf(c.get("payload"))));
                assertEquals(norm(new Yaml().load(String.valueOf(c.get("decoded")))), norm(out));
            }));
        }
        return tests;
    }
}
