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
 * CR-2026-154 (PS-566): a negative zero is reported as 0 and written as the positive-zero
 * word, for every floating-point type. The cases are shared with the reference, the
 * generated codec, Go and C# in tests/cr-2026-154-cases.yaml.
 */
public class CR2026154Test {

    @SuppressWarnings("unchecked")
    private static Map<String, Object> doc() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-154-cases.yaml");
        return new Yaml().load(Files.readString(file));
    }

    @SuppressWarnings("unchecked")
    @TestFactory
    List<DynamicTest> encodeCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : (List<Map<String, Object>>) doc().get("encode_cases")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                // SnakeYAML reads "-0.0" as a Double with its sign, "-0" would be the integer 0.
                Map<String, Object> input = new Yaml().load(String.valueOf(c.get("input")));
                EncodeResult result = schema.encode(input);
                assertTrue(result.isSuccess(), result.getErrors().toString());
                assertEquals(String.valueOf(c.get("payload")),
                        HexFormat.of().formatHex(result.getPayload()));
            }));
        }
        return tests;
    }

    @SuppressWarnings("unchecked")
    @TestFactory
    List<DynamicTest> decodeAndReencodeCases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : (List<Map<String, Object>>) doc().get("decode_cases")) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Map<String, Object> decoded = schema.decode(
                        HexFormat.of().parseHex(String.valueOf(c.get("payload"))));
                double x = ((Number) decoded.get("x")).doubleValue();
                assertTrue(x == 0 && 1 / x > 0, "reported " + decoded.get("x") + ", want a positive 0");
                EncodeResult result = schema.encode(decoded);
                assertTrue(result.isSuccess(), result.getErrors().toString());
                assertEquals(String.valueOf(c.get("reencode")),
                        HexFormat.of().formatHex(result.getPayload()));
            }));
        }
        return tests;
    }
}
