package dim;

import com.zaxxer.hikari.HikariDataSource;
import org.jooq.DSLContext;
import org.jooq.SQLDialect;
import org.jooq.exception.DataAccessException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.xbill.DNS.*;
import org.xbill.DNS.Record;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.io.Reader;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.Properties;

import static org.jooq.impl.DSL.using;
import static dim.Util.name;

public class Compare {
    private final HikariDataSource dimDataSource;
    private static final Logger log = LoggerFactory.getLogger(PdnsOutput.class);

    Compare(Properties config) {
        dimDataSource = new HikariDataSource(PdnsOutput.dimDbConfig(config));
    }

    private static Properties readConfig(String fileName) {
        Properties config = new Properties();
        try (Reader r = new BufferedReader(new FileReader(fileName))) {
            config.load(r);
        } catch (IOException e) {
            System.err.println("Error reading config file " + fileName);
        }
        return config;
    }

    public static void main(String[] args) {
        new Compare(readConfig("pdns-output.properties")).run();
    }

    private void run() {
        try (Connection conn = dimDataSource.getConnection()) {
            DSLContext dim = using(conn, SQLDialect.MYSQL);
            String query = "SELECT rr.id, zone.name as zone, rr.name, rr.ttl, rr.type, rr.value" +
                    " FROM rr" +
                    " JOIN zoneview ON rr.zoneview_id=zoneview.id" +
                    " JOIN zone ON zone.id=zoneview.zone_id ORDER BY rr.id";
            for (org.jooq.Record row : dim.resultQuery(query).fetchLazy()) {
                long id = row.get("id", Long.class);
                String zone = row.get("zone", String.class);
                String rrname = row.get("name", String.class);
                String type = row.get("type", String.class);
                String value = row.get("value", String.class);
                Record r = Record.fromString(name(rrname), Type.value(type), DClass.IN, 0, value, name(zone));
                System.out.println(id + "\t" + type + "\t" + r.pdnsContent());
            }
        } catch (SQLException | DataAccessException e) {
            log.error("SQL error", e);
        } catch (IOException e) {
            e.printStackTrace();
        }
    }
}
