# PyFundaments: A Secure Python Architecture
##### v. 1.0.1 dev

## Description

This project, named **PyFundaments**, provides a robust and secure Python architecture. Its core mission is not to be a monolithic framework, but to establish a layered, modular foundation—or "fundament"—of essential services. This structure ensures that every application starts on a verified and secure base, with a focus on stability, code clarity, and a security-first mindset from the ground up.

> [!NOTE]  
> you must create your app/bot in app/* or you get an error! Than uncoment
> ```
>from app.app import start_application
>await start_application(fundaments)
>```
> in main.py



-----

## Table of Contents

  - [Project Structure](#project-structure)
  - [The Role of `main.py`](#the-role-of-mainpy)
  - [Configuration (`.env`)](#configuration-env)
  - [Installation](#installation)
  - [Getting Started](#getting-started)
  - [Module Documentation](#module-documentation)
  - [Notes](#notes)

-----

## Project Structure

```

├── main.py # run main!
├── README.md
├── requirements.txt
├── .gitignore
├── .env.example
├── app/
│   └── ...
│   └── app.py # sandboxed app run!
│   └── tools.py
│   └── provider.py
│   └── models.py
│   └── db_sync.py

├── fundaments/ # do not touch! 
│   ├── access_control.py
│   ├── config_handler.py
│   ├── encryption.py
│   ├── postgresql.py
│   ├── security.py
│   └── user_handler.py
└── docs/
    ├── access_control.py.md
    ├── encryption.py.md
    ├── postgresql.py.md
    ├── security.py.md
    └── user_handler.py.md

```
-----

## The Role of `main.py`

`main.py` is the **only entry point** and acts as the first line of defense for the application.

### Responsibilities

  - [x] **Validate Dependencies**: It checks for all necessary core modules in `fundaments/` and exits immediately if any are missing.
  - [x] **Conditional Environment Loading**: It uses the `config_handler` to load available configuration variables and initializes only the services for which configuration is present.
  - [x] **Initialize Services**: It creates instances of available services (PostgreSQL, encryption, access control, user handling) based on environment configuration, collecting them into a single service dictionary.
  - [x] **Graceful Degradation**: Services that cannot be initialized are skipped with warnings, allowing applications to run with partial functionality.
  - [x] **Decouple App Logic**: It hands off the prepared and verified services to `app/app.py`, allowing the main application to focus purely on its business logic without worrying about low-level setup.

-----

## Configuration (`.env`)

Application settings are managed using a `.env` file. **Never commit this file to version control.**

The framework uses **conditional loading** - only services with available configuration are initialized. This allows different application types to use only what they need.

A `.env.example` file is provided to show available variables. Create a copy named `.env` and configure only what your application requires.

### Core Configuration (Optional based on app type)

```text
# Database connection (required only for database-using apps)
DATABASE_URL="postgresql://user:password@host:port/database?sslmode=require"

# Encryption keys (required only for apps using encryption)
MASTER_ENCRYPTION_KEY="your_256_bit_key_here"
PERSISTENT_ENCRYPTION_SALT="your_unique_salt_here"

# Logging configuration (optional)
LOG_LEVEL="INFO"
LOG_TO_TMP="false"
ENABLE_PUBLIC_LOGS="true"
```

### Application Examples

**Discord Bot:** Only needs `BOT_TOKEN`, no database or encryption required.
**ML Pipeline:** Only needs `DATABASE_URL` for data access.
**Web Application:** May need all services for full functionality.

-----

## Installation

```bash
pip install -r requirements.txt
```

-----

## Getting Started

1.  **Configure**: Create your `.env` file from the `.env.example` and set only the variables your application type requires.
2.  **Run**: Execute the `main.py` script to start the application.

```bash
python main.py
```

The framework will automatically detect available configuration and load only the necessary services.

-----

## Module Documentation

Each security-relevant core module is documented in the `docs/` directory:

| Module              | Description                              | Documentation                                     |
| ------------------- | ---------------------------------------- | -------------------------------------------------|
| `access_control.py` | Role-based access management             | [access\_control.py.md](docs/access_control.py.md)|
| `config_handler.py` | Universal configuration loader           | [config\_handler.py.md](docs/config_handler.py.md)|
| `encryption.py`     | Cryptographic routines                   | [encryption.py.md](docs/encryption.py.md)         |
| `postgresql.py`     | Secure, asynchronous database access     | [postgresql.py.md](docs/postgresql.py.md)         |
| `user_handler.py`   | Authentication and identity management   | [user\_handler.py.md](docs/user_handler.py.md)     |
| `security.py`       | Central security orchestration layer     | [security.py.md](docs/security.py.md)             |

-----

## License

This project is licensed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

### Section 4: Additional Restrictions and Ethical Use Policy

This project is released under the permissive Apache 2.0 License, which is intended to provide broad freedom for use and modification.  
However, in the interest of fostering a safe and responsible community, we include the following **mandatory ethical use restrictions**:

Use of this project (or derivatives) is **strictly prohibited** for purposes that:

  - **Promote Hatred or Discrimination**  
      Includes hate speech, incitement to violence, or discrimination based on race, religion, gender, orientation, etc.

  - **Facilitate Illegal Activities**  
      Use to conduct or support criminal activity is forbidden.

  - **Spread Malicious Content**  
      This includes pornography, malware, spyware, or other harmful payloads.

We believe that freedom in development must be coupled with responsibility.  
**Violation of these terms constitutes a breach of license and will trigger takedown actions** including legal and technical responses.

*Volkan Kücükbudak*

-----

## Credits

- Developed and maintained by the PyFundaments project.  
-  Core components inspired by best practices in modular app architecture and OWASP security principles.
- Third-party libraries are credited in their respective module docs and comply with open-source terms.

-----

## Notes

    Security-first by design.  
    If one piece is missing or unsafe — the app does not run.  
    Zero tolerance for guesswork.

> Give a ⭐ if you find the structure helpful.


## Changelog 
###### Version 1.0.0 -> 1.0.1
### PyFundaments Refactoring

#### Modified Files

**1. main.py - Conditional Service Loading**
- **Added:** Environment-based conditional service initialization
- **Added:** Graceful fallback when services can't be initialized (warning instead of crash)
- **Added:** Conditional logging configuration (LOG_LEVEL, LOG_TO_TMP, ENABLE_PUBLIC_LOGS)
- **Added:** Smart dependency management (access_control & user_handler only load if database available)
- **Added:** Safe shutdown (only close DB pool if it was initialized)
- **Result:** Framework now supports partial service loading for different app types

**2. app/app.py - Proper Service Injection**
- **Removed:** Direct service imports and instantiation
- **Added:** Services received as parameter from main.py
- **Modified:** start_application() now takes fundaments dictionary as parameter
- **Added:** Conditional service usage based on what main.py provides
- **Added:** Examples for different app types (database-only, database-free modes)
- **Result:** Clean separation - main.py handles initialization, app.py uses services

**3. fundaments/config_handler.py - Universal Configuration Loader**
- **Removed:** REQUIRED_KEYS validation (moved to main.py)
- **Modified:** Now loads ALL environment variables without validation
- **Added:** Helper methods: get_bool(), get_int(), has(), get_all()
- **Added:** Safe defaults and type conversion
- **Result:** Config handler never needs updates, works with any ENV variables

**4. .env.example - Conditional Configuration Template**
- **Added:** Clear separation between core and optional dependencies
- **Added:** Logging configuration options
- **Added:** App-specific configuration examples (Discord, ML, Web)
- **Added:** Comments explaining when to use each option
- **Result:** Users only configure what they need, no unused variables

#### Architecture Improvements
- **Conditional Loading:** Framework only loads needed services based on available ENV vars
- **Merge-Safe Structure:** User code (app/, config_handler.py) protected from framework updates
- **Zero Breaking Changes:** Updates only affect fundaments/ directory
- **Enterprise-Level Merging:** Community can safely pull framework updates</document_content></document>
