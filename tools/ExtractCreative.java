/** Maintainer-only, version-pinned Mojang Java 1.21.9 reflection exporter.
 * Run on JDK 21 with the client JAR and its official runtime libraries on the classpath.
 * Arguments: creative_tabs.tsv resolved_tags.tsv. No game files are shipped by this tool.
 */
import java.lang.reflect.*;
import java.nio.file.*;
import java.io.*;
import java.util.*;

public class ExtractCreative {
    public static void main(String[] args) throws Exception {
        Class.forName("ac").getMethod("a").invoke(null);
        Class.forName("aml").getMethod("a").invoke(null);
        Object lookup = Class.forName("pi").getMethod("a").invoke(null);
        lookup = bindTags(lookup, args[1]);
        Object flags = Class.forName("dcz").getField("h").get(null);
        for (Method method : Class.forName("dgh").getDeclaredMethods()) {
            if (method.getName().equals("a") && method.getParameterCount() == 3
                && method.getParameterTypes()[1] == boolean.class && method.getReturnType() == boolean.class) {
                method.invoke(null, flags, true, lookup);
            }
        }
        Class<?> registries = Class.forName("mo");
        Object tabs = registries.getField("ak").get(null);
        Object items = registries.getField("h").get(null);
        Object blocks = registries.getField("e").get(null);
        Method key = Class.forName("jx").getMethod("b", Object.class);
        Method display = Class.forName("dgg").getMethod("l");
        Method stackItem = Class.forName("dhp").getMethod("h");
        Method blockItem = Class.forName("duv").getMethod("h");
        try (BufferedWriter writer = Files.newBufferedWriter(Path.of(args[0]))) {
            for (Object tab : (Iterable<?>) tabs) {
                Set<String> ids = new TreeSet<>();
                for (Object stack : (Collection<?>) display.invoke(tab)) {
                    ids.add(key.invoke(items, stackItem.invoke(stack)).toString());
                }
                writer.write("T\t" + key.invoke(tabs, tab) + "\t" + String.join(",", ids) + "\n");
            }
            for (Object block : (Iterable<?>) blocks) {
                writer.write("B\t" + key.invoke(blocks, block) + "\t" + key.invoke(items, blockItem.invoke(block)) + "\n");
            }
            Class<?> processor = Class.forName("fdo");
            Field replacements = processor.getDeclaredField("c");
            replacements.setAccessible(true);
            Map<String,String> ordered = new TreeMap<>();
            for (Map.Entry<?,?> entry : ((Map<?,?>) replacements.get(processor.getField("b").get(null))).entrySet())
                ordered.put(key.invoke(blocks, entry.getKey()).toString(), key.invoke(blocks, entry.getValue()).toString());
            for (Map.Entry<String,String> entry : ordered.entrySet())
                writer.write("R\t" + entry.getKey() + "\t" + entry.getValue() + "\n");
        }
        System.out.println("Extracted creative tabs and block item forms.");
    }

    private static Object bindTags(Object provider, String file) throws Exception {
        Map<String, Map<String, Set<String>>> tags = new HashMap<>();
        for (String line : Files.readAllLines(Path.of(file))) {
            String[] parts = line.split("\t", -1);
            tags.computeIfAbsent("minecraft:" + parts[0], x -> new HashMap<>())
                .put("minecraft:" + parts[1], new HashSet<>(Arrays.asList(parts[2].split(","))));
        }
        List<Object> registries = new ArrayList<>();
        for (Object value : (Iterable<?>) Class.forName("mo").getField("aM").get(null)) registries.add(value);
        try (java.util.stream.Stream<?> stream = (java.util.stream.Stream<?>) Class.forName("jm$a").getMethod("c").invoke(provider)) {
            stream.forEach(registries::add);
        }
        Class<?> registryLookup = Class.forName("jm$b");
        Class<?> registry = Class.forName("jx");
        Class<?> keyClass = Class.forName("ami");
        Class<?> locationClass = Class.forName("amj");
        Method location = keyClass.getMethod("a");
        Method tagCreate = Class.forName("bdj").getMethod("a", keyClass, locationClass);
        Method parse = locationClass.getMethod("a", String.class);
        Method holderKey = Class.forName("jk$c").getMethod("h");
        Method holderTags = Class.forName("jk$c").getDeclaredMethod("a", Collection.class);
        holderTags.setAccessible(true);
        Map<String,Object> wrappedLookups = new LinkedHashMap<>();
        Constructor<?> named = Class.forName("jo$c").getDeclaredConstructor(Class.forName("jn"), Class.forName("bdj"));
        named.setAccessible(true);
        Method namedBind = Class.forName("jo$c").getDeclaredMethod("b", List.class);
        namedBind.setAccessible(true);
        for (Object reg : registries) {
            boolean isLookup = registryLookup.isInstance(reg);
            Object regKey = (isLookup ? registryLookup : registry).getMethod("g").invoke(reg);
            String regName = location.invoke(regKey).toString();
            Map<String, Set<String>> group = tags.getOrDefault(regName, Map.of());
            Map<Object, List<Object>> bound = new HashMap<>();
            Method elements = (isLookup ? Class.forName("jm") : registry).getMethod("c");
            try (java.util.stream.Stream<?> stream = (java.util.stream.Stream<?>) elements.invoke(reg)) {
                for (Object holder : stream.toList()) {
                    String id = location.invoke(holderKey.invoke(holder)).toString();
                    List<Object> membership = new ArrayList<>();
                    for (Map.Entry<String, Set<String>> entry : group.entrySet()) {
                        if (entry.getValue().contains(id)) {
                            Object tag = tagCreate.invoke(null, regKey, parse.invoke(null, entry.getKey()));
                            membership.add(tag);
                            bound.computeIfAbsent(tag, x -> new ArrayList<>()).add(holder);
                        }
                    }
                    holderTags.invoke(holder, membership);
                }
            }
            if (isLookup) {
                Map<Object,Object> namedTags = new HashMap<>();
                for (Map.Entry<Object,List<Object>> entry : bound.entrySet()) {
                    Object set = named.newInstance(reg, entry.getKey());
                    namedBind.invoke(set, entry.getValue());
                    namedTags.put(entry.getKey(), set);
                }
                Object wrapped = Proxy.newProxyInstance(ExtractCreative.class.getClassLoader(), new Class<?>[]{registryLookup}, (proxy, method, arguments) -> {
                    if (arguments != null && arguments.length == 1 && Class.forName("bdj").isInstance(arguments[0])) {
                        Object set = namedTags.get(arguments[0]);
                        if (method.getReturnType() == Optional.class) return Optional.ofNullable(set);
                        if (set != null) return set;
                        throw new IllegalArgumentException("Unknown vanilla tag: " + arguments[0]);
                    }
                    if (method.getName().equals("e") && method.getParameterCount() == 0) return namedTags.values().stream();
                    return method.invoke(reg, arguments);
                });
                wrappedLookups.put(regName, wrapped);
            }
        }
        return Class.forName("jm$a").getMethod("a", java.util.stream.Stream.class).invoke(null, wrappedLookups.values().stream());
    }
}
