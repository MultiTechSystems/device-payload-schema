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
 * CR-2026-194 (PS-436, PS-437, PS-438, PS-440, PS-270): a one-field tlv tag's "[*]" and "[!n]"
 * keys rank below exact keys, whatever the declaration order, and a tlv has no {@code default}
 * case. The cases are shared with the reference, Go and C# in tests/cr-2026-194-cases.yaml.
 */
public class CR2026194Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-194-cases.yaml");
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
                for (Object o : (List<?>) c.get("decodes")) {
                    Map<?, ?> d = (Map<?, ?>) o;
                    Map<String, Object> out = schema.decode(HexFormat.of().parseHex(String.valueOf(d.get("payload"))));
                    assertEquals(norm(new Yaml().load(String.valueOf(d.get("decoded")))), norm(out));
                }
            }));
        }
        return tests;
    }
}
