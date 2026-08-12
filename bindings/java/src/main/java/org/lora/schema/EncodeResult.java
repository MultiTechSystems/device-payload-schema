// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * The outcome of encoding a data map to payload bytes.
 *
 * <p>Mirrors the reference interpreter's {@code EncodeResult}: a field that cannot be
 * encoded is recorded against the payload rather than aborting it, so one unrecoverable
 * value does not cost every other field its bytes. A field the data does not carry is a
 * warning and encodes as zero; a field whose value cannot have produced any legal bytes
 * is an error.
 *
 * <p>The decoders in this binding throw, and still do. Encoding differs because it has
 * inherently lossy cases - a {@code lookup} default label stands for every unmapped
 * value, a rounding stage discarded precision - and a caller needs to be told which
 * fields those were, not just that the call failed.
 */
public final class EncodeResult {

    private final byte[] payload;
    private final List<String> warnings;
    private final List<String> errors;

    EncodeResult(byte[] payload, List<String> warnings, List<String> errors) {
        this.payload = payload;
        this.warnings = warnings == null ? new ArrayList<>() : warnings;
        this.errors = errors == null ? new ArrayList<>() : errors;
    }

    /** The encoded bytes. Present even when {@link #getErrors()} is non-empty. */
    public byte[] getPayload() {
        return payload;
    }

    public List<String> getWarnings() {
        return Collections.unmodifiableList(warnings);
    }

    public List<String> getErrors() {
        return Collections.unmodifiableList(errors);
    }

    /** True when no field failed to encode. Warnings do not clear success. */
    public boolean isSuccess() {
        return errors.isEmpty();
    }

    @Override
    public String toString() {
        StringBuilder hex = new StringBuilder(payload.length * 2);
        for (byte b : payload) {
            hex.append(String.format("%02x", b));
        }
        return "EncodeResult[payload=" + hex + ", warnings=" + warnings.size()
                + ", errors=" + errors + "]";
    }
}
