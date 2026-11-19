import pytest
from dim.messages import Messages
from dim.transaction import retryable_transaction
from sqlalchemy.exc import IntegrityError, OperationalError


@pytest.fixture(autouse=True)
def clean_messages():
    Messages.clear()
    yield
    Messages.clear()


# 1. Normal successful call without error
def test_retryable_transaction_success():
    attempts = 0

    @retryable_transaction
    def successful():
        nonlocal attempts
        attempts += 1
        return "OK"

    assert successful() == "OK"
    assert attempts == 1


# 2. Retry on "Lock wait timeout exceeded" (fails on attempt 1, succeeds on attempt 2, attempts == 2)
def test_retryable_transaction_retries_on_lock_wait_timeout():
    attempts = 0

    @retryable_transaction
    def retry_lock():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError(
                "SELECT 1",
                {},
                Exception("Lock wait timeout exceeded; try restarting transaction"),
            )
        return "RECOVERED"

    assert retry_lock() == "RECOVERED"
    assert attempts == 2


# 3. Retry on "Deadlock found" (fails on attempt 1, succeeds on attempt 2, attempts == 2)
def test_retryable_transaction_retries_on_deadlock():
    attempts = 0

    @retryable_transaction
    def retry_deadlock():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError(
                "SELECT 1",
                {},
                Exception(
                    "Deadlock found when trying to get lock; try restarting transaction"
                ),
            )
        return "RECOVERED"

    assert retry_deadlock() == "RECOVERED"
    assert attempts == 2


# 4. Retry on "try restarting transaction" / Concurrency collision
def test_retryable_transaction_retries_on_record_changed_error():
    """MySQL 1020 collision when pdns-output and middleware access outputupdate concurrently."""
    attempts = 0

    @retryable_transaction
    def retry_collision():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError(
                "INSERT INTO outputupdate",
                {},
                Exception(
                    "(1020, \"Record has changed since last read in table 'outputupdate'; try restarting transaction\")"
                ),
            )
        return "RECOVERED"

    assert retry_collision() == "RECOVERED"
    assert attempts == 2


# 5. Messages isolation: Warnings/messages added during failed attempt are discarded via Messages.restore()
def test_retryable_transaction_messages_isolation_on_retry():
    attempts = 0

    @retryable_transaction
    def func_with_messages():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            Messages.warn("Transient warning from failed attempt 1")
            raise OperationalError(
                "SELECT 1", {}, Exception("Lock wait timeout exceeded")
            )
        Messages.info("Success message from attempt 2")
        return "DONE"

    result = func_with_messages()
    assert result == "DONE"
    assert attempts == 2
    messages = Messages.get()
    assert len(messages) == 1
    assert messages[0] == (Messages.INFO, "Success message from attempt 2")


# 6. Retry exhaustion: When lock errors persist across all 3 attempts, original OperationalError is raised (attempts == 3)
def test_retryable_transaction_reraises_operational_error_after_max_attempts():
    attempts = 0

    @retryable_transaction
    def always_fails():
        nonlocal attempts
        attempts += 1
        raise OperationalError("SELECT 1", {}, Exception("Lock wait timeout exceeded"))

    with pytest.raises(OperationalError) as exc_info:
        always_fails()

    assert attempts == 3
    assert "Lock wait timeout exceeded" in str(exc_info.value)
    # Ensure it is definitely not masked by an AttributeError
    assert not isinstance(exc_info.value, AttributeError)


# 7. Non-lock OperationalError: e.g. "Lost connection to MySQL server" or "Unknown column" re-raised immediately on attempt 1
@pytest.mark.parametrize(
    "error_msg",
    [
        "Lost connection to MySQL server during query",
        "Unknown column 'foo' in 'field list'",
    ],
)
def test_retryable_transaction_reraises_non_lock_operational_error(error_msg):
    attempts = 0

    @retryable_transaction
    def non_lock_error():
        nonlocal attempts
        attempts += 1
        raise OperationalError("SELECT 1", {}, Exception(error_msg))

    with pytest.raises(OperationalError) as exc_info:
        non_lock_error()

    assert attempts == 1
    assert error_msg in str(exc_info.value)
    assert not isinstance(exc_info.value, AttributeError)


# 8. Other exceptions: ValueError, KeyError, IntegrityError are NOT caught and propagate immediately (attempts == 1)
@pytest.mark.parametrize(
    ("exc_type", "exc_instance"),
    [
        (ValueError, ValueError("Invalid argument passed")),
        (KeyError, KeyError("missing_key")),
        (
            IntegrityError,
            IntegrityError("INSERT INTO zone", {}, Exception("Duplicate entry")),
        ),
    ],
)
def test_retryable_transaction_does_not_catch_other_exceptions(exc_type, exc_instance):
    attempts = 0

    @retryable_transaction
    def fail_with_other_exc():
        nonlocal attempts
        attempts += 1
        raise exc_instance

    with pytest.raises(exc_type) as exc_info:
        fail_with_other_exc()

    assert attempts == 1
    assert exc_info.type is exc_type


# 9. Messages restored on total failure: Initial state of Messages is restored when all attempts fail
def test_retryable_transaction_restores_messages_on_total_failure():
    Messages.info("Pre-existing initial message")
    initial_messages = list(Messages.get())
    attempts = 0

    @retryable_transaction
    def always_fails():
        nonlocal attempts
        attempts += 1
        Messages.warn(f"Failed attempt {attempts}")
        raise OperationalError("SELECT 1", {}, Exception("Lock wait timeout exceeded"))

    with pytest.raises(OperationalError) as exc_info:
        always_fails()

    assert attempts == 3
    assert "Lock wait timeout exceeded" in str(exc_info.value)
    # Messages must be restored to initial state prior to transaction
    assert Messages.get() == initial_messages
