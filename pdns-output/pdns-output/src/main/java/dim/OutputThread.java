package dim;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import org.jooq.exception.DataAccessException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.sql.DataSource;
import java.net.URI;
import java.net.URISyntaxException;
import java.util.List;
import java.util.concurrent.BlockingDeque;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static dim.Util.transaction;
import static dim.Util.transactionCall;

/**
 * Process transactions from a single output
 */
class OutputThread implements Runnable {
    private static final Logger log = LoggerFactory.getLogger(OutputThread.class);

    final long output_id;
    private final String name;
    private final String dbUri;
    private final DataSource dimDataSource;
    private final long retryInterval;
    private final boolean printTxn;
    private final long maxQuerySize;
    private final BlockingDeque<List<OutputUpdate>> transactions = new LinkedBlockingDeque<>();
    private DataSource dataSource;
    private AtomicInteger pending = new AtomicInteger();

    OutputThread(long output_id, String name, String dbUri, DataSource dimDataSource, long retryInterval,
                 boolean printTxn, long maxQuerySize) {
        this.output_id = output_id;
        this.name = name;
        this.dbUri = dbUri;
        this.dimDataSource = dimDataSource;
        this.retryInterval = retryInterval;
        this.printTxn = printTxn;
        this.maxQuerySize = maxQuerySize;
    }

    boolean offer(List<OutputUpdate> t) {
        pending.addAndGet(t.size());
        return transactions.offer(t);
    }

    boolean hasPending() {
        return pending.get() > 0;
    }

    int pendingSize() {
        return pending.get();
    }

    @Override
    public void run() {
        Thread.currentThread().setName("ot-" + name);
        try {
            transaction(getDataSource(), pdns -> {
                if (pdns.fetch("SHOW COLUMNS FROM records LIKE 'rev_name'").size() != 1) {
                    updateOutputStatus(output_id, "Table records is missing 'rev_name' column");
                    log.error("Output {} is missing the records.rev_name column", name);
                    System.exit(1);
                }
            });
            while (true) {
                List<OutputUpdate> actions = transactions.poll(1, TimeUnit.HOURS);
                if (actions == null) {
                    boolean exists = transactionCall(dimDataSource, dim ->
                            dim.fetchOne("SELECT COUNT(*) FROM output WHERE id=?", output_id).get(0, int.class) > 0);
                    if (!exists) {
                        log.info("Output {} was deleted", name);
                        return;
                    }
                } else {
                    while (!processTransaction(actions))
                        Thread.sleep(retryInterval);
                    pending.addAndGet(-actions.size());
                }
            }
        } catch (InterruptedException e) {
            log.info("Thread for output {} interrupted", name);
        }
    }

    /**
     * Process transaction and return true in case of success
     */
    private boolean processTransaction(List<OutputUpdate> actions) {
        if (actions == null || actions.size() == 0) {
            log.warn("Empty transaction");
            return true;
        }
        String transaction_id = actions.get(0).transaction;

        Timer timer = new Timer();
        log.info("Applying transaction {}", transaction_id);
        try {
            transaction(dimDataSource, dim -> {
                try (DatabaseNamedLock ignored = new DatabaseNamedLock(dim, "output_" + output_id, 1)) {
                    transaction(getDataSource(), pdns -> {
                        for (OutputUpdate action : actions)
                            action.applyUpdate(dim, pdns, maxQuerySize);
                    });
                    dim.execute("DELETE FROM outputupdate WHERE output_id=? AND transaction=?", output_id,
                            transaction_id);
                } catch (LockTimeout e) {
                    // TODO is it safe to throw here?
                    throw new DataAccessException(e.getMessage());
                }
            });
        } catch (Exception e) {
            log.error("Error processing transaction {}", transaction_id, e);
            updateOutputStatus(output_id, e.getMessage());
            return false;
        }
        log.info("Finished transaction {} in {}", transaction_id, timer.elapsed());
        updateOutputStatus(output_id, "OK");
        if (printTxn)
            System.out.println(transaction_id);
        return true;
    }

    private void updateOutputStatus(long output_id, String status) {
        try {
            if (status == null)
                status = "Unknown error";
            String abbrev = status.substring(0, Math.min(status.length(), 255));
            transaction(dimDataSource, dim -> dim.execute("UPDATE output SET status=?, last_run=NOW() WHERE id=?",
                    abbrev, output_id));
        } catch (Exception e) {
            log.warn("Failed to update output status for {}", output_id, e);
        }
    }

    private DataSource getDataSource() {
        if (dataSource == null) {
            URI url;
            try {
                url = new URI(dbUri);
            } catch (URISyntaxException e) {
                throw new RuntimeException("Invalid db_uri for output " + name + "(" + dbUri + ")", e);
            }
            HikariConfig pdnsdb = new HikariConfig();
            pdnsdb.setPoolName(name);
            if (url.getScheme() == null || !url.getScheme().equals("mysql"))
                throw new RuntimeException("Invalid scheme in db_uri for output " + name + "(" + dbUri + ")");
            else
                pdnsdb.setDataSourceClassName("com.mysql.cj.jdbc.MysqlDataSource");
            pdnsdb.addDataSourceProperty("serverName", url.getHost());
            if (url.getPort() != -1)
                pdnsdb.addDataSourceProperty("port", url.getPort());
            pdnsdb.addDataSourceProperty("databaseName", url.getPath().substring(1));
            if (url.getUserInfo() != null) {
                final String[] split = url.getUserInfo().split(":", 2);
                pdnsdb.addDataSourceProperty("user", split[0]);
                if (split.length > 1)
                    pdnsdb.addDataSourceProperty("password", split[1]);
            }
            pdnsdb.setAutoCommit(false);
            pdnsdb.addDataSourceProperty("serverTimezone", "UTC");
            dataSource = new HikariDataSource(pdnsdb);
        }
        return dataSource;
    }
}
