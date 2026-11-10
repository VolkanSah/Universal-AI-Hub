# PyFundaments – Function Overview

## `main.py`

| Function                  | Description                                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `initialize_fundaments()` | Asynchronously initializes services based on available environment variables. Returns a dictionary of services (`None` if not initialized). |
| `main()`                  | Application entry point. Calls `initialize_fundaments()`, loads `app/app.py`, closes DB pool on shutdown.                                   |

---

## `fundaments/config_handler.py` – `ConfigHandler`

| Function                 | Description                                                        |
| ------------------------ | ------------------------------------------------------------------ |
| `__init__()`             | Loads `.env` via `python-dotenv` and system environment variables. |
| `load_all_config()`      | Stores all non-empty environment variables in `self.config`.       |
| `get(key)`               | Returns value as string or `None`.                                 |
| `get_bool(key, default)` | Parses boolean values (`true/1/yes/on`).                           |
| `get_int(key, default)`  | Returns integer value or `default` on failure.                     |
| `has(key)`               | Returns `True` if key exists and is not empty.                     |
| `get_all()`              | Returns copy of full configuration dictionary.                     |
| `config_service`         | Global singleton instance.                                         |

---

## `fundaments/postgresql.py`

| Function                                              | Description                                                                         |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `enforce_cloud_security(dsn_url)`                     | Enforces `sslmode=require`, applies timeouts, removes incompatible DSN options.     |
| `mask_dsn(dsn_url)`                                   | Removes credentials from DSN for logging.                                           |
| `ssl_runtime_check(conn)`                             | Verifies active SSL connection.                                                     |
| `init_db_pool(dsn_url)`                               | Creates asyncpg pool (min=1, max=10) and runs SSL check.                            |
| `close_db_pool()`                                     | Gracefully closes connection pool.                                                  |
| `execute_secured_query(query, *params, fetch_method)` | Executes parameterized query (`fetch`, `fetchrow`, `execute`) with reconnect logic. |

---

## `fundaments/encryption.py` – `Encryption`

| Function                               | Description                                                      |
| -------------------------------------- | ---------------------------------------------------------------- |
| `generate_salt()`                      | Generates secure 16-byte hex salt.                               |
| `__init__(master_key, salt)`           | Derives AES-256 key via PBKDF2-SHA256 (480k iterations).         |
| `encrypt(data)`                        | Encrypts string using AES-256-GCM. Returns `{data, nonce, tag}`. |
| `decrypt(encrypted_data, nonce, tag)`  | Decrypts data. Raises `InvalidTag` if tampered.                  |
| `encrypt_file(source_path, dest_path)` | Encrypts file in 8192-byte chunks.                               |
| `decrypt_file(source_path, dest_path)` | Decrypts file using stored nonce and tag.                        |

---

## `fundaments/access_control.py` – `AccessControl`

| Function                                           | Description                          |
| -------------------------------------------------- | ------------------------------------ |
| `__init__(user_id)`                                | Initializes with optional user ID.   |
| `has_permission(permission_name)`                  | Checks if user has permission.       |
| `get_user_permissions()`                           | Returns all user permissions.        |
| `get_user_roles()`                                 | Returns assigned roles.              |
| `assign_role(role_id)`                             | Assigns role to user.                |
| `remove_role(role_id)`                             | Removes role from user.              |
| `get_all_roles()`                                  | Returns all roles.                   |
| `get_all_permissions()`                            | Returns all permissions.             |
| `create_role(name, description)`                   | Creates new role and returns ID.     |
| `update_role_permissions(role_id, permission_ids)` | Replaces all permissions for a role. |
| `get_role_permissions(role_id)`                    | Returns role permissions.            |

---

## `fundaments/user_handler.py`

### `Database` (SQLite Wrapper)

| Function                  | Description                                       |
| ------------------------- | ------------------------------------------------- |
| `execute(query, params)`  | Executes query and commits.                       |
| `fetchone(query, params)` | Returns single row.                               |
| `fetchall(query, params)` | Returns all rows.                                 |
| `close()`                 | Closes connection.                                |
| `setup_tables()`          | Creates `users` and `sessions` tables if missing. |

---

### `Security` (Password Utilities)

| Function                            | Description                                  |
| ----------------------------------- | -------------------------------------------- |
| `hash_password(password)`           | Hashes password (PBKDF2-SHA256 via passlib). |
| `verify_password(password, hashed)` | Verifies password against hash.              |
| `regenerate_session(session_id)`    | Generates new UUID session ID.               |

---

### `UserHandler`

| Function                                  | Description                                            |
| ----------------------------------------- | ------------------------------------------------------ |
| `login(username, password, request_data)` | Authenticates user, validates state, creates session.  |
| `logout()`                                | Removes session from DB and memory.                    |
| `is_logged_in()`                          | Checks if active session exists.                       |
| `is_admin()`                              | Checks session `is_admin` flag.                        |
| `validate_session(request_data)`          | Validates session against IP and User-Agent.           |
| `lock_account(username)`                  | Locks user account.                                    |
| `reset_failed_attempts(username)`         | Resets failed login counter.                           |
| `increment_failed_attempts(username)`     | Increments failed attempts and locks after 5 failures. |

---

## `fundaments/security.py` – `Security` (Orchestrator)

| Function                                       | Description                                                           |
| ---------------------------------------------- | --------------------------------------------------------------------- |
| `__init__(services)`                           | Initializes with required services. Raises `RuntimeError` if missing. |
| `user_login(username, password, request_data)` | Performs login and session validation.                                |
| `check_permission(user_id, permission_name)`   | Delegates permission check.                                           |
| `encrypt_data(data)`                           | Encrypts data if encryption service is available.                     |
| `decrypt_data(encrypted_data, nonce, tag)`     | Decrypts data or returns `None` on failure.                           |

---

## `fundaments/debug.py` – `PyFundamentsDebug`

| Function          | Description                                             |
| ----------------- | ------------------------------------------------------- |
| `__init__()`      | Reads debug-related environment variables.              |
| `_setup_logger()` | Configures logging handlers.                            |
| `run()`           | Outputs runtime diagnostics when debug mode is enabled. |

---

## `app/app.py`

| Function                        | Description                                                           |
| ------------------------------- | --------------------------------------------------------------------- |
| `start_application(fundaments)` | Receives initialized service dictionary and starts application logic. |

---

## Architecture Notes

* `UserHandler` uses internal SQLite.
* `AccessControl` uses PostgreSQL via `execute_secured_query`.
* `security.py` `Security` is the orchestrator layer.
* All services are optional.
