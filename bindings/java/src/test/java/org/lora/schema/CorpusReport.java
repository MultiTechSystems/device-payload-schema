// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Per-vector reporting for the corpus conformance runner, for tools/verdicts-gate.py.
 *
 * <p>A pass count against a floor cannot say which vectors an implementation fails, so a
 * vector that decodes correctly in Python and differently here is invisible whenever the
 * total still clears the floor. Two optional environment variables change that; with
 * neither set the runner behaves exactly as before:
 *
 * <ul>
 *   <li>{@code CORPUS_REPORT=/path/file.json} also writes one {@code {schema, index,
 *       vector, status, detail}} entry per vector, status being pass, fail, error or skip.
 *   <li>{@code CORPUS_ONLY=a/x.yaml,b/y.yaml} runs only these schemas (relative to
 *       schemas/devices; a leading {@code schemas/devices/} is accepted). The floor is not
 *       applied to a restricted run, because it counts the whole corpus.
 *   <li>{@code CORPUS_ROOT=/some/dir} walks this directory instead of schemas/devices, so a
 *       scratch schema can be run without being placed in the repository.
 * </ul>
 *
 * <p>The JSON is written by hand: the test classpath has snakeyaml and JUnit and no JSON
 * library, and five string fields do not justify adding one.
 */
final class CorpusReport {
    private final String path = System.getenv("CORPUS_REPORT");
    private final Set<String> only;
    private final List<String> entries = new ArrayList<>();

    CorpusReport() {
        String raw = System.getenv("CORPUS_ONLY");
        if (raw == null || raw.isBlank()) {
            only = null;
        } else {
            only = new LinkedHashSet<>();
            for (String item : raw.split(",")) {
                String rel = relative(item.trim());
                if (!rel.isEmpty()) only.add(rel);
            }
        }
    }

    static String relative(String path) {
        String rel = path.replace('\\', '/');
        return rel.startsWith("schemas/devices/") ? rel.substring("schemas/devices/".length()) : rel;
    }

    /** Whether CORPUS_ONLY or CORPUS_ROOT moved the run off the whole corpus. */
    boolean restricted() {
        String root = System.getenv("CORPUS_ROOT");
        return only != null || (root != null && !root.isEmpty());
    }

    int restrictedCount() {
        return only == null ? 0 : only.size();
    }

    boolean includes(String rel) {
        return only == null || only.contains(rel);
    }

    void add(String schema, int index, Object vector, String status, String detail) {
        if (path == null || path.isEmpty()) return;
        String name = vector == null ? "" : String.valueOf(vector);
        String text = detail == null ? "" : detail;
        if (text.length() > 200) text = text.substring(0, 200);
        entries.add("{\"schema\": " + quote(schema) + ", \"index\": " + index
                + ", \"vector\": " + quote(name) + ", \"status\": " + quote(status)
                + ", \"detail\": " + quote(text) + "}");
    }

    void write() throws IOException {
        if (path == null || path.isEmpty()) return;
        String body = "[\n" + String.join(",\n", entries) + (entries.isEmpty() ? "" : "\n") + "]\n";
        Files.writeString(Path.of(path), body, StandardCharsets.UTF_8);
        System.out.printf("corpus report: %d vectors written to %s%n", entries.size(), path);
    }

    static String quote(String text) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
                    else out.append(c);
                }
            }
        }
        return out.append('"').toString();
    }
}
