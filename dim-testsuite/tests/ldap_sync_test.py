from unittest.mock import MagicMock, patch
from tests.util import DatabaseTest
from dim.models import User, db
from dim.ldap_sync import add_user, sync_users


class LDAPSyncTest(DatabaseTest):
    """
    Unit tests for the LDAP synchronization and user import features.
    Uses unittest.mock to simulate LDAP connections and entries.
    """

    @patch("dim.ldap_sync.LDAP")
    def test_add_standard_user(self, mock_ldap_class):
        """
        Test that add_user() successfully imports a standard user
        from the default LDAP_USER_BASE and sets is_pseudo to False.
        """
        # Step 1: Mock the LDAP class instantiation to return our mock connection
        mock_ldap = MagicMock()
        mock_ldap_class.return_value = mock_ldap

        # Step 2: Create a real User model instance with mock standard user data
        fake_user = User(
            username="test_standard",
            ldap_cn="Test Standard",
            ldap_uid=1234,
            department_number=1,
        )

        # Step 3: Configure users() method to return our fake user in a list
        mock_ldap.users.return_value = [fake_user]

        # Step 4: Call the manual import function we want to test
        add_user("test_standard")

        # Step 5: Assert that the user was successfully created in the test DB
        user = User.query.filter_by(username="test_standard").first()
        self.assertIsNotNone(user)
        self.assertEqual(user.ldap_cn, "Test Standard")
        self.assertEqual(user.ldap_uid, 1234)
        self.assertEqual(user.department_number, 1)
        self.assertFalse(user.is_pseudo)

    @patch("dim.ldap_sync.LDAP")
    def test_add_pseudo_user(self, mock_ldap_class):
        """
        Test that add_user() falls back to LDAP_PSEUDO_USER_BASE
        and imports a pseudo user with is_pseudo set to True.
        """
        # Step 1: Configure a fake pseudo user base DN in the app config
        self.app.config["LDAP_PSEUDO_USER_BASE"] = "ou=pseudo,dc=example,dc=com"

        # Step 2: Mock the LDAP class instantiation to return our mock connection
        mock_ldap = MagicMock()
        mock_ldap_class.return_value = mock_ldap

        # Step 3: Create a real User model instance with mock pseudo user data
        user_pseudo = User(
            username="test_pseudo",
            ldap_cn="Test Pseudo",
            ldap_uid=5678,
            department_number=None,
            register=False,
        )

        # Step 4: Write a dynamic side_effect function to handle dual queries
        # Returns the fake user only when searching the configured pseudo base
        def mock_users(search_filter, base_dn=None):
            if base_dn == "ou=pseudo,dc=example,dc=com":
                return [user_pseudo]
            return []

        mock_ldap.users.side_effect = mock_users

        # Step 5: Call the manual import function
        add_user("test_pseudo")

        # Step 6: Assert that the user was created in the DB as a pseudo user (is_pseudo = True)
        user = User.query.filter_by(username="test_pseudo").first()
        self.assertIsNotNone(user)
        self.assertEqual(user.ldap_cn, "Test Pseudo")
        self.assertEqual(user.ldap_uid, 5678)
        self.assertEqual(user.department_number, None)
        self.assertTrue(user.is_pseudo)

    @patch("dim.ldap_sync.LDAP")
    def test_ldap_sync(self, mock_ldap_class):
        """
        Test that sync_users() successfully reconciles and synchronizes existing
        database users, performs separate queries for standard and pseudo-users,
        auto-classifies 'None' status users, deletes obsolete users, and preserves
        local-only accounts.
        """
        # Step 1: Mock the LDAP class instantiation and configure the pseudo base DN
        mock_ldap = MagicMock()
        mock_ldap_class.return_value = mock_ldap
        self.app.config["LDAP_PSEUDO_USER_BASE"] = "ou=pseudo,dc=example,dc=com"

        # Step 2: Setup various user types in the database
        alice = User(
            username="alice",
            ldap_cn="Alice",
            ldap_uid=1,
            department_number=1,
            is_pseudo=False,
        )
        pseudo_test = User(
            username="pseudo_test",
            ldap_cn="Pseudo Test",
            ldap_uid=2,
            department_number=None,
            is_pseudo=None,  # Unclassified user, should be auto-classified as pseudo
        )
        bob = User(
            username="bob",
            ldap_cn="Bob",
            ldap_uid=3,
            department_number=2,
            is_pseudo=False,
        )
        charlie = User(
            username="charlie",
            ldap_cn=None,
            ldap_uid=None,  # Local-only user (no ldap_uid), should NOT be deleted
            department_number=None,
            is_pseudo=None,
        )

        db.session.add_all([alice, pseudo_test, bob, charlie])
        db.session.commit()

        # Step 3: Write a dynamic side_effect function to simulate LDAP searches
        # - Alice: returned with updated properties (New CN and Department)
        # - Pseudo Test: returned only in the pseudo base query
        # - Bob: omitted from LDAP results entirely (triggers deletion)
        def mock_ldap_search(search_filter, base_dn=None):
            ldap_pseudo_test = User(
                username="pseudo_test",
                ldap_cn="Pseudo Test",
                ldap_uid=2,
                department_number=None,
                register=False,
            )
            ldap_alice = User(
                username="alice",
                ldap_cn="Alice Mueller",
                ldap_uid=1,
                department_number=3,
                register=False,
            )
            if base_dn is None:
                return [ldap_alice]
            elif base_dn == "ou=pseudo,dc=example,dc=com":
                return [ldap_pseudo_test]
            return []

        mock_ldap.users.side_effect = mock_ldap_search

        # Step 4: Execute the synchronization process
        sync_users(mock_ldap)

        # Step 5: Assert that mock users() was called with precise optimized filters
        self.assertEqual(mock_ldap.users.call_count, 2)
        calls = mock_ldap.users.call_args_list

        # Verify first call (Standard search filter)
        first_filter = calls[0][0][0]
        first_kwargs = calls[0][1]
        self.assertIn("o=alice", first_filter)
        self.assertIn("o=pseudo_test", first_filter)
        self.assertIn("o=bob", first_filter)
        self.assertIn("o=charlie", first_filter)
        self.assertIsNone(first_kwargs.get("base_dn"))

        # Verify second call (Pseudo search filter)
        second_filter = calls[1][0][0]
        second_kwargs = calls[1][1]
        self.assertIn("uid=pseudo_test", second_filter)
        self.assertNotIn("uid=alice", second_filter)
        self.assertNotIn("uid=bob", second_filter)
        self.assertIn("uid=charlie", second_filter)
        self.assertEqual(second_kwargs.get("base_dn"), "ou=pseudo,dc=example,dc=com")

        # Step 6: Verify database reconciliation results
        # Alice should be updated with new attributes
        db_alice = User.query.filter_by(username="alice").first()
        self.assertIsNotNone(db_alice)
        self.assertEqual(db_alice.ldap_cn, "Alice Mueller")
        self.assertEqual(db_alice.department_number, 3)

        # Pseudo Test should be auto-classified as a pseudo user (is_pseudo = True)
        db_pseudo = User.query.filter_by(username="pseudo_test").first()
        self.assertIsNotNone(db_pseudo)
        self.assertTrue(db_pseudo.is_pseudo)

        # Bob should be completely deleted from the database
        db_bob = User.query.filter_by(username="bob").first()
        self.assertIsNone(db_bob)

        # Charlie (local-only user) should still exist and remain untouched
        db_charlie = User.query.filter_by(username="charlie").first()
        self.assertIsNotNone(db_charlie)
        self.assertIsNone(db_charlie.is_pseudo)

    @patch("dim.ldap_sync.LDAP")
    def test_sync_users_fail_fast(self, mock_ldap_class):
        """
        Test that sync_users() immediately raises an exception and aborts
        reconciliation if any LDAP query fails, ensuring database and data integrity.
        """
        # Step 1: Mock the LDAP class instantiation
        mock_ldap = MagicMock()
        mock_ldap_class.return_value = mock_ldap

        # Step 2: Setup an active user in the database
        alice = User(
            username="alice",
            ldap_cn="Alice",
            ldap_uid=1,
            is_pseudo=False,
        )
        db.session.add(alice)
        db.session.commit()

        # Step 3: Force the LDAP users() query to raise an exception (simulate network loss)
        mock_ldap.users.side_effect = Exception("LDAP Connection Failure")

        # Step 4: Verify that sync_users() propagates the exception to abort the transaction
        with self.assertRaises(Exception):
            sync_users(mock_ldap)

        # Step 5: Assert that Alice is STILL in the database (no accidental deletion occurred)
        db_alice = User.query.filter_by(username="alice").first()
        self.assertIsNotNone(db_alice)
