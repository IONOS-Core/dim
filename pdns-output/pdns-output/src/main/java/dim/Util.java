package dim;

import org.jooq.DSLContext;
import org.jooq.SQLDialect;
import org.xbill.DNS.Name;
import org.xbill.DNS.TextParseException;

import javax.sql.DataSource;
import java.util.function.Consumer;
import java.util.function.Function;

import static org.jooq.impl.DSL.using;

public class Util {
    static void transaction(DataSource dataSource, Consumer<DSLContext> c) {
        DSLContext dsl = using(dataSource, SQLDialect.MYSQL);
        dsl.transaction(cfg -> c.accept(using(cfg)));
    }

    static <T> T transactionCall(DataSource dataSource, Function<DSLContext, T> c) {
        DSLContext dsl = using(dataSource, SQLDialect.MYSQL);
        return dsl.transactionResult(cfg -> c.apply(using(cfg)));
    }

    static Name name(String s) {
        try {
            if (s.endsWith("."))
                return new Name(s);
            else
                return new Name(s + ".");
        } catch (TextParseException e) {
            throw new RuntimeException("Parsing error", e);
        }
    }
}
