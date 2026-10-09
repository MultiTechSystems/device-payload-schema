// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

/**
 * The input context of {@link Schema#interpret} (PS-495): the TS013 uplink's FPort and
 * {@code recvTime}, and the device's EUI. Each may be null, meaning not supplied; its
 * {@code _meta} key is then omitted (PS-341).
 *
 * @param fPort    the FPort, selecting the port entry as {@link Schema#decodeWithPort} does
 * @param recvTime an ISO 8601 string, or a number of Unix seconds
 * @param devEUI   16 hex digits, optionally separated by {@code -}, {@code :} or spaces
 */
public record InputContext(Integer fPort, Object recvTime, String devEUI) { }
