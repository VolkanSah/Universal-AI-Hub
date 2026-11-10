# Secure User Authentication and Session Management

### Overview

This module, `user_handler.py`, is a standalone component for handling user authentication, session management, and security. It provides a secure, yet easy-to-use, system for managing user logins and protecting against common attacks like brute-force attempts and session fixation.

It is designed to be a direct Python equivalent of a user management class you might find in a web application built with a traditional framework, offering similar functionality and a focus on security.

-----

### Core Security Features

  - **Password Hashing:** Passwords are not stored in plain text. The module uses `passlib` with `pbkdf2_sha256` for robust, salted password hashing, making it nearly impossible to retrieve the original password from the database.
  - **Session Fixation Prevention:** The `login` method regenerates the session ID after a successful authentication, ensuring that an attacker cannot hijack a pre-existing session.
  - **Brute-Force Protection:** The system tracks failed login attempts. After a configurable number of failures (e.g., 5 attempts), it automatically locks the user's account to prevent further brute-force attacks.
  - **Session Validation:** Sessions are not just validated by an ID. The module also checks the user's IP address and user agent to ensure the session hasn't been hijacked.
  - **Data Storage:** A simple `SQLite` database is used as a placeholder. In a production environment, this would be replaced by a more robust and scalable solution like `PostgreSQL`.

-----

### Module Components

1.  **`Database` Class (Placeholder):**
    A simple wrapper for SQLite to simulate database interactions. This is where you would integrate a proper ORM or a more powerful database driver in a production application.

2.  **`Security` Class:**
    A static class responsible for core security functions. It handles password hashing and verification using `passlib` and includes a method to simulate session ID regeneration.

3.  **`UserHandler` Class:**
    The main class for handling user-related logic. It contains methods for:

      - `login(username, password, request_data)`: Verifies user credentials and establishes a secure session.
      - `logout()`: Terminates the user's session.
      - `is_logged_in()`: Checks if a user has an active session.
      - `is_admin()`: Determines if the logged-in user has administrator privileges.
      - `validate_session()`: Checks if the session is valid based on request details.
      - `lock_account()`: Manually locks a user's account.
      - `increment_failed_attempts()`: Increments the failed login counter and locks the account if a threshold is reached.

-----

### Example Usage

The `if __name__ == "__main__":` block at the end of the file provides a complete example of how to use the module:

1.  **Setup:** Initializes the database and creates the necessary tables.
2.  **User Registration:** Demonstrates how to create a regular user and an admin user with securely hashed passwords.
3.  **Successful Login:** Shows a successful login attempt, which creates a new session.
4.  **Logout:** Illustrates how to terminate the session.
5.  **Brute-Force Protection Test:** Simulates multiple failed login attempts to demonstrate the account-locking mechanism.
6.  **Account Reset:** Shows how to manually reset failed attempts to re-enable an account.

This module provides a robust and well-documented foundation for building a secure and reliable user authentication system.
