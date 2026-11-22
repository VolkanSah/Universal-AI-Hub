# Module: `config_handler.py`

## Description

The `config_handler` module is a core component of the application's security fundament. It provides a centralized, secure, and robust mechanism for managing all critical environment variables. By enforcing early validation, it prevents the application from starting in an insecure or misconfigured state.

## Core Principles

  - **Centralized Source of Truth**: All environment variables are loaded and managed from a single point.
  - **Fail-Fast Mechanism**: The application exits immediately if any required configuration key is missing. This prevents runtime errors and potential security vulnerabilities from a broken setup.
  - **Separation of Concerns**: It decouples the loading and validation of configurations from the business logic of other modules.

## Required Environment Variables

The `ConfigHandler` is configured to specifically look for the following keys, which must be present in the `.env` file or the system's environment variables.

| Key | Description | Example |
| :--- | :--- | :--- |
| `DATABASE_URL` | The full DSN (Data Source Name) string for the PostgreSQL database. Supports local connections and cloud providers like Neon.tech. | `postgresql://user:password@host:port/database?sslmode=require` |
| `MASTER_ENCRYPTION_KEY` | A 256-bit key used for symmetric encryption across the application. **Crucial for data security.** | `532c6614...` |
| `PERSISTENT_ENCRYPTION_SALT` | A unique salt used with the master key to enhance cryptographic security. | `a0b7e8d2...` |

## Usage

Other modules, such as `main.py`, import the singleton instance of the `ConfigHandler` to access validated configuration values safely.

```python
# In main.py or any other fundament module
from fundaments.config_handler import config_service

# To get a validated value
db_url = config_service.get("DATABASE_URL")
master_key = config_service.get("MASTER_ENCRYPTION_KEY")
```
