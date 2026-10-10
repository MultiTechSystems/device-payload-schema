package org.lora.schema;

import java.util.regex.Pattern;

import org.yaml.snakeyaml.DumperOptions;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.Constructor;
import org.yaml.snakeyaml.nodes.NodeId;
import org.yaml.snakeyaml.nodes.Tag;
import org.yaml.snakeyaml.representer.Representer;
import org.yaml.snakeyaml.resolver.Resolver;

/**
 * A SnakeYAML resolver that reads only {@code true} and {@code false} as booleans, as
 * YAML 1.2 does.
 *
 * <p>SnakeYAML follows YAML 1.1, where {@code yes}, {@code no}, {@code on} and
 * {@code off} are booleans too, so a boolean literal's {@code value: yes} arrived as
 * {@code true} and passed PS-520 unseen. Go's yaml.v3 and C#'s reader already keep them
 * as text, and so does the Python reference's schema loader.
 */
final class CoreResolver extends Resolver {
    private static final Pattern CORE_BOOL = Pattern.compile("^(?:true|True|TRUE|false|False|FALSE)$");

    @Override
    public Tag resolve(NodeId kind, String value, boolean implicit) {
        Tag tag = super.resolve(kind, value, implicit);
        if (Tag.BOOL.equals(tag) && !CORE_BOOL.matcher(value).matches()) {
            return Tag.STR;
        }
        return tag;
    }

    /** A Yaml reader for schema documents. */
    static Yaml yaml() {
        DumperOptions dumper = new DumperOptions();
        LoaderOptions loader = new LoaderOptions();
        return new Yaml(new Constructor(loader), new Representer(dumper), dumper, loader,
                new CoreResolver());
    }
}
