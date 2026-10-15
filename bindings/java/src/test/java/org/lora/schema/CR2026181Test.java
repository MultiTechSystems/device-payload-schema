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
 * CR-2026-181 (PS-601, PS-602; PS-081, PS-527, PS-551 amended): a bytes, hex or base64
 * value is written exactly as given. The cases are shared with the reference, the
 * generated codec, Go and C# in tests/cr-2026-181-cases.yaml.
 */
public class CR2026181Test {

    @SuppressWarnings("unchecked")
    @TestFactory
    List<DynamicTest> encodeCases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-181-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : (List<Map<String, Object>>) doc.get("cases")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> input = new Yaml().load(String.valueOf(c.get("input")));
                EncodeResult result = schema.encode(input);
                if (c.containsKey("error")) {
                    String want = String.valueOf(c.get("error"));
                    assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                            "want '" + want + "', got " + result.getErrors());
                    assertFalse(result.isSuccess());
                } else {
                    assertTrue(result.isSuccess(), result.getErrors().toString());
                    assertEquals(String.valueOf(c.get("payload")),
                            HexFormat.of().formatHex(result.getPayload()));
                }
            }));
        }
        return tests;
    }
}
