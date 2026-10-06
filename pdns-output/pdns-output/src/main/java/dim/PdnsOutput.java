package dim;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import gmprsa.NativeRSAProvider;
import org.apache.commons.cli.*;
import org.apache.logging.log4j.core.config.Configurator;
import org.jooq.DSLContext;
import org.jooq.Record;
import org.jooq.SQLDialect;
import org.jooq.exception.DataAccessException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.*;
import java.security.Provider;
import java.security.Security;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;
import java.util.stream.Collectors;

import static dim.Util.transactionCall;
import static org.jooq.impl.DSL.using;

public class PdnsOutput {
    private static final Logger log = LoggerFactory.getLogger(PdnsOutput.class);
    private static final String defaultConfigFile = "/etc/dim/pdns-output.properties";
    private static final String defaultLogConfigFile = "/etc/dim/log4j2.properties";
    private static final Options options = new Options();

    static {
        options.addOption("h", "help", false, "print usage information");
        options.addOption("c", "config", true, String.format("configuration file (default: %s)", defaultConfigFile));
        options.addOption("l", "logconfig", true, String.format("log configuration file (default: %s)", defaultLogConfigFile));
    }

    private final Properties config = new Properties();
    private final HikariDataSource dimDataSource;
    private final boolean printTxn;
    private final long maxQuerySize;
    private final Map<Long, OutputThread> outputs = new ConcurrentHashMap<>();
    private final Executor executor = Executors.newCachedThreadPool();
    /**
     * Max number of outputupdate actions queued for processing. It will automatically grow to accommodate large
     * transactions
     */
    private int queueSize = 50000;

    private PdnsOutput(String configFile) {
        readConfigFromResource("/pdns-output.properties");  // defaults file to ensure all configuration keys exist
        readConfig(configFile);
        printTxn = Boolean.parseBoolean(config.getProperty("printTxn"));
        maxQuerySize = Long.parseLong(config.getProperty("maxQuerySize"));
        dimDataSource = new HikariDataSource(dimDbConfig(config));

        if (Boolean.parseBoolean(config.getProperty("useNativeCrypto"))) {
            Provider provider = new NativeRSAProvider();
            Security.insertProviderAt(provider, 1);
        }
    }

    public static void main(String[] args) {
        CommandLineParser parser = new PosixParser();
        CommandLine cmd;
        try {
            cmd = parser.parse(options, args);
            if (cmd.hasOption("help")) {
                printHelp();
                return;
            }
        } catch (ParseException e) {
            System.err.println("Parsing command line arguments failed: " + e.getMessage());
            printHelp();
            return;
        }

        PdnsOutput pdnsOutput = new PdnsOutput(cmd.getOptionValue("config", defaultConfigFile));
        pdnsOutput.run();
    }

    private void run() {
        try {
            pollOutputQueue();
        } catch (InterruptedException e) {
            log.info("Interrupted");
        }
    }

    private void pollOutputQueue() throws InterruptedException {
        double lockTimeout = Double.parseDouble(config.getProperty("lockTimeout"));
        long pollDelay = (long) Double.parseDouble(config.getProperty("pollDelay")) * 1000;
        while (true) {
            try (Connection conn = dimDataSource.getConnection()) {
                DSLContext dim = using(conn, SQLDialect.MYSQL);
                try (DatabaseNamedLock ignored = new DatabaseNamedLock(dim, "pdns_poller", lockTimeout)) {
                    grabTransactions(dim).forEach(this::queueTransaction);
                    Thread.sleep(pollDelay);
                }
            } catch (SQLException | DataAccessException e) {
                log.error("SQL error", e);
                Thread.sleep(10_000);
            } catch (LockTimeout e) {
            }
        }
    }

    /**
     * Reads transactions from the outputupdate table and returns them grouped by output and transaction.
     * <p>
     * Only transactions from outputs with empty queues are returned. This ensures no duplicate transactions are queued.
     */
    private List<List<OutputUpdate>> grabTransactions(DSLContext dim) {
        List<List<OutputUpdate>> transactions = new ArrayList<>();
        List<Long> pending = outputs.values().stream()
                .filter(OutputThread::hasPending)
                .map(o -> o.output_id)
                .collect(Collectors.toList());
        int pendingSize = outputs.values().stream().mapToInt(OutputThread::pendingSize).sum();
        if (queueSize > pendingSize) {
            int limit = queueSize - pendingSize;
            String excludePending = "";
            if (!pending.isEmpty())
                excludePending = " AND output.id NOT IN (" +
                        pending.stream().map(x -> "?").collect(Collectors.joining(",")) + ")";
            String query = "SELECT outputupdate.* FROM outputupdate JOIN output ON output.id = outputupdate.output_id" +
                    " WHERE output.plugin='pdns-db' " + excludePending + " ORDER BY outputupdate.id LIMIT " + limit;

            // Group updates by transaction and output_id, keeping transactions in their original order
            OutputUpdate last = null;
            Map<Long, List<OutputUpdate>> group = new HashMap<>();
            List<OutputUpdate> updates = dim.resultQuery(query, pending.toArray()).fetchInto(OutputUpdate.class);
            for (OutputUpdate update : updates) {
                if (last == null || !update.transaction.equals(last.transaction)) {
                    transactions.addAll(group.values());
                    group.clear();
                }
                group.computeIfAbsent(update.output_id, k -> new ArrayList<>()).add(update);
                last = update;
            }
            // If the limit was hit the last transaction must be discarded as it may be incomplete, otherwise add it
            if (updates.size() < limit) {
                transactions.addAll(group.values());
            } else if (pendingSize == 0 && queueSize < Integer.MAX_VALUE / 2) {
                // No pending transactions but we hit the limit so the next transaction must be larger than queueSize
                // Let's double it and try again later
                queueSize *= 2;
                log.info("Increasing queueSize to {}", queueSize);
            }
        }
        log.debug("Grabbed {} transactions", transactions.size());
        return transactions;
    }

    private void queueTransaction(List<OutputUpdate> actions) {
        long output_id = actions.get(0).output_id;
        long retryInterval = (long) Double.parseDouble(config.getProperty("retryInterval")) * 1000;
        OutputThread thread = outputs.computeIfAbsent(output_id, key -> {
            Record output = transactionCall(dimDataSource, dim ->
                    dim.fetchOne("SELECT * FROM output WHERE id=?", output_id));
            if (output == null)
                throw new DataAccessException(String.format("output %d was deleted", output_id));

            // Make sure the connection pool for the dim db can handle all outputs simultaneously
            // we need at least:
            // outputs.size()  + 1 for the OutputThread we add now + 1 for the polling thread
            dimDataSource.setMaximumPoolSize(outputs.size() + 5);

            OutputThread ret = new OutputThread(output_id, output.get("name", String.class),
                    output.get("db_uri", String.class), dimDataSource, retryInterval, printTxn, maxQuerySize);
            executor.execute(ret);
            return ret;
        });
        thread.offer(actions);
    }

    public static HikariConfig dimDbConfig(Properties config) {
        HikariConfig dimdb = new HikariConfig();
        dimdb.setPoolName("dimdb");
        dimdb.setDataSourceClassName("com.mysql.cj.jdbc.MysqlDataSource");
        dimdb.setAutoCommit(false);
        dimdb.addDataSourceProperty("serverTimezone", "UTC");
        for (Map.Entry<Object, Object> entry : config.entrySet()) {
            String key = (String) entry.getKey();
            if (key.startsWith("db.")) {
                key = key.substring(3);
                dimdb.addDataSourceProperty(key, entry.getValue());
            }
        }
        return dimdb;
    }

    private static void printHelp() {
        new HelpFormatter().printHelp("java -jar pdns-output.jar [OPTION]...", options);
    }

    private void readConfigFromResource(String resourceName) {
        InputStream in = getClass().getResourceAsStream(resourceName);
        if (in == null)
            throw new RuntimeException("Resource " + resourceName + " not found");
        try (InputStream bin = new BufferedInputStream(in)) {
            config.load(bin);
        } catch (IOException e) {
            log.debug("Error reading config from resource {}", resourceName, e);
        }
    }

    private void readConfig(String fileName) {
        try (Reader r = new BufferedReader(new FileReader(fileName))) {
            config.load(r);
        } catch (IOException e) {
            log.debug("Error reading config file {}", fileName, e);
        }
    }
}
