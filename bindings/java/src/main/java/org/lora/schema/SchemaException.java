package org.lora.schema;

public class SchemaException extends RuntimeException {
    
    public SchemaException(String message) {
        super(message);
    }
    
    public SchemaException(String message, Throwable cause) {
        super(message, cause);
    }
    
    public static class DecodeException extends SchemaException {
        public DecodeException(String message) {
            super(message);
        }
        
        public DecodeException(String message, Throwable cause) {
            super(message, cause);
        }
    }
    
    /**
     * A read or skip past the end of the payload, less any {@code reserve} (PS-535).
     *
     * <p>Thrown unnamed by {@link DecodeContext#read} and {@link DecodeContext#peek}; the
     * innermost field that made the read restates it as {@code Field '<name>': ... (PS-535)}
     * ({@link #named}), and every enclosing field passes it through unchanged. The text is
     * the spec prototype's, so a cross-language diff compares equal strings.
     */
    public static class BufferUnderflow extends DecodeException {
        private final boolean named;
        private final String detail;

        public BufferUnderflow(String detail) {
            super(detail);
            this.named = false;
            this.detail = detail;
        }

        private BufferUnderflow(String message, String detail) {
            super(message);
            this.named = true;
            this.detail = detail;
        }

        public boolean isNamed() { return named; }

        /** The unnamed text: "Buffer underflow: need ...". */
        public String getDetail() { return detail; }

        /** How PS-535 identifies a field: its name, else what it is. */
        public static String label(String name, String kind) {
            if (name != null && !name.isEmpty()) return "'" + name + "'";
            return "an unnamed " + (kind == null || kind.isEmpty() ? "field" : kind);
        }

        /**
         * How PS-535 identifies a byte_group: by its members, as the spec prototype does -
         * {@code byte_group of 'f', 'w'}, every named member in declaration order, internal
         * {@code _} members included - else {@code an unnamed byte_group} (CR-2026-131).
         */
        public static String byteGroupLabel(java.util.List<String> memberNames) {
            java.util.List<String> quoted = new java.util.ArrayList<>();
            for (String name : memberNames) {
                if (name != null && !name.isEmpty()) quoted.add("'" + name + "'");
            }
            return quoted.isEmpty() ? "an unnamed byte_group"
                    : "byte_group of " + String.join(", ", quoted);
        }

        /** This error as the field's, unless an inner field has already named it. */
        public BufferUnderflow named(String name, String kind) {
            return namedAs(label(name, kind));
        }

        /** This error identified by a ready-made PS-535 label. */
        public BufferUnderflow namedAs(String label) {
            if (named) return this;
            return new BufferUnderflow("Field " + label + ": " + detail + " (PS-535)", detail);
        }
    }

    public static class EncodeException extends SchemaException {
        public EncodeException(String message) {
            super(message);
        }
        
        public EncodeException(String message, Throwable cause) {
            super(message, cause);
        }
    }
    
    /**
     * An encode error whose text already names its field in full (PS-537), reported
     * verbatim: the encoder does not prefix it with "Error encoding x: ".
     */
    public static class FieldEncodeException extends EncodeException {
        public FieldEncodeException(String message) {
            super(message);
        }
    }

    public static class ParseException extends SchemaException {
        public ParseException(String message) {
            super(message);
        }
        
        public ParseException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
